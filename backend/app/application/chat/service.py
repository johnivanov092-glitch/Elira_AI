"""Chat orchestration: shared helpers + non-streaming agent execution.

Extracted from agents_service.py.  All module-level helpers (_tl, guards,
monitoring, memory recall, history) live here so that both service.py and
stream_service.py can import them without circular dependencies.
"""
from __future__ import annotations

import logging
import re
import time as _time
from typing import Any

from app.application.chat.auto_skills import (
    _FILE_TRIGGERS_EXCEL,
    _FILE_TRIGGERS_WORD,
    _build_prompt,
    _get_and_clear_attachments,
    _maybe_generate_files,
    _pending_attachments,
)
from app.application.chat.context_builder import (
    collect_context as build_chat_context,
    strip_frontend_project_context as build_strip_frontend_project_context,
)
from app.application.chat.prompt_builder import (
    compose_human_style_rules as _compose_human_style_rules,
)
from app.core.config import pick_model_for_route
from app.infrastructure.search.web_search import (
    do_temporal_web_search as _infra_do_temporal_web_search,
)
from app.application.monitoring.agent_monitor import record_agent_run_metric
from app.application.monitoring.agent_sandbox import (
    SandboxPolicyError,
    preflight_or_raise,
    resolve_effective_agent_id,
)
from app.application.chat.chat_service import run_chat
from app.application.policy.identity_guard import guard_identity_response
from app.application.persona.persona_service import observe_dialogue
from app.application.planning.planner_v2_service import PlannerV2Service
from app.application.policy.provenance_guard import guard_provenance_response
from app.application.agents.reflection_loop_service import run_reflection_loop
from app.infrastructure.db.run_history_service import RunHistoryService
from app.application.memory.smart_memory import extract_and_save, get_relevant_context, is_memory_command
from app.application.tools.tool_service import run_tool
from app.application.event_bus import emit_event
from app.application.agents.agent_registry import record_agent_run, resolve_agent

try:
    from app.application.memory.rag_memory_service import get_rag_context
    _HAS_RAG = True
except ImportError:
    _HAS_RAG = False

    def get_rag_context(*a, **kw):  # type: ignore[misc]
        return ""


logger = logging.getLogger(__name__)

_HISTORY = RunHistoryService()
_REFLECTION_ROUTES = {"code", "project"}
_MAX_HISTORY_PAIRS = 10
_DIRECT_PERSONAL_MEMORY_RE = re.compile(
    r"(?iu)^\s*(?:как\s+меня\s+зовут|ты\s+знаешь\s+как\s+меня\s+зовут"
    r"|what\s+is\s+my\s+name|do\s+you\s+know\s+my\s+name)\s*\??\s*$"
)


# ─── timeline helper ─────────────────────────────────────────────────────────


def _tl(timeline: list, step: str, title: str, status: str, detail: str) -> None:
    timeline.append({"step": step, "title": title, "status": status, "detail": detail})


# ─── post-processing guards ──────────────────────────────────────────────────


def _apply_identity_guard(
    user_input: str, answer_text: str, timeline: list
) -> dict[str, Any]:
    guard = guard_identity_response(user_input, answer_text, persona_name="Elira")
    if guard.get("changed"):
        _tl(timeline, "identity_guard", "Идентичность Elira", "done",
            guard.get("reason", "identity_rewrite"))
    return guard


def _apply_provenance_guard(
    user_input: str, answer_text: str, timeline: list
) -> dict[str, Any]:
    guard = guard_provenance_response(user_input, answer_text)
    if guard.get("changed"):
        _tl(timeline, "provenance_guard", "Ответ без служебных источников", "done",
            guard.get("reason", "source_hidden"))
    return guard


# ─── Agent OS hooks ──────────────────────────────────────────────────────────


def _emit_agent_os_event(
    *, event_type: str, source_agent_id: str = "", payload: dict[str, Any] | None = None
) -> None:
    try:
        emit_event(event_type=event_type, source_agent_id=source_agent_id, payload=payload or {})
    except Exception:
        logger.debug("event_bus_emit_failed", exc_info=True)


def _record_agent_os_monitoring(
    *,
    agent_id: str,
    run_id: str,
    route: str,
    model_name: str,
    ok: bool,
    duration_ms: int,
    streaming: bool,
    num_ctx: int,
    selected_tools: list[str] | None,
) -> None:
    try:
        record_agent_run_metric(
            agent_id=agent_id,
            run_id=run_id,
            route=route,
            model_name=model_name,
            ok=ok,
            duration_ms=duration_ms,
            streaming=streaming,
            num_ctx=int(num_ctx or 0),
            tools=list(selected_tools or []),
        )
    except Exception:
        logger.debug("agent_monitor_record_failed", exc_info=True)


# ─── memory helpers ──────────────────────────────────────────────────────────


def _is_direct_personal_memory_query(user_input: str) -> bool:
    return bool(_DIRECT_PERSONAL_MEMORY_RE.search(user_input or ""))


def _should_recall_memory_context(
    user_input: str, route: str, temporal: dict[str, Any] | None
) -> bool:
    temporal = temporal or {}
    if is_memory_command(user_input):
        return False
    if route == "research" and temporal.get("mode") == "hard" and temporal.get("freshness_sensitive"):
        return False
    return True


def _get_memory_recall_limits(user_input: str) -> tuple[int, int]:
    if _is_direct_personal_memory_query(user_input):
        return (1, 0)
    return (5, 3)


# ─── history ─────────────────────────────────────────────────────────────────


def _trim_history(h: list, max_pairs: int = _MAX_HISTORY_PAIRS) -> list:
    if not h:
        return []
    limit = max_pairs * 2
    if len(h) <= limit:
        return list(h)
    return list(h[:2]) + list(h[-(limit - 2):])


# ─── context assembly ────────────────────────────────────────────────────────


def _strip_frontend_project_context(user_input: str) -> str:
    return build_strip_frontend_project_context(user_input)


def _do_temporal_web_search(query, timeline, tool_results, temporal=None, web_plan=None):
    return _infra_do_temporal_web_search(
        query, timeline, tool_results, temporal=temporal, web_plan=web_plan, tl=_tl
    )


def _collect_context(**kwargs):
    return build_chat_context(
        run_tool_func=run_tool,
        append_timeline=_tl,
        temporal_web_search_func=_do_temporal_web_search,
        **kwargs,
    )


# ─── execute_chat_agent (non-streaming) ──────────────────────────────────────


def execute_chat_agent(
    *,
    model_name,
    profile_name,
    user_input,
    session_id=None,
    agent_id=None,
    use_memory=True,
    use_library=True,
    use_reflection=False,
    history=None,
    num_ctx=8192,
    use_web_search=True,
    use_python_exec=True,
    use_image_gen=True,
    use_file_gen=True,
    use_http_api=True,
    use_sql=True,
    use_screenshot=True,
    use_encrypt=True,
    use_archiver=True,
    use_converter=True,
    use_regex=True,
    use_translator=True,
    use_csv=True,
    use_webhook=True,
    use_plugins=True,
):
    _agent_start = _time.monotonic()

    _registry_agent = None
    if agent_id:
        try:
            _registry_agent = resolve_agent(agent_id=agent_id)
            if _registry_agent:
                if _registry_agent.get("system_prompt"):
                    profile_name = _registry_agent.get("name_ru") or profile_name
                if _registry_agent.get("model_preference"):
                    model_name = _registry_agent["model_preference"]
        except Exception:
            pass

    _effective_agent_id = resolve_effective_agent_id(
        agent_id=agent_id,
        profile_name=profile_name,
        registry_agent=_registry_agent,
    )
    history = _trim_history(history or [])
    _skill_flags = {
        "web_search": use_web_search, "python_exec": use_python_exec,
        "image_gen": use_image_gen, "file_gen": use_file_gen,
        "http_api": use_http_api, "sql": use_sql, "screenshot": use_screenshot,
        "encrypt": use_encrypt, "archiver": use_archiver, "converter": use_converter,
        "regex": use_regex, "translator": use_translator, "csv_analysis": use_csv,
        "webhook": use_webhook, "plugins": use_plugins,
    }
    _disabled_skills = {k for k, v in _skill_flags.items() if not v}
    timeline, tool_results = [], []
    planner = PlannerV2Service()
    raw_user_input = user_input
    planner_input = _strip_frontend_project_context(user_input)
    run = _HISTORY.start_run(raw_user_input)
    _agent_os_source_id = _effective_agent_id
    _emit_agent_os_event(
        event_type="agent.run.started",
        source_agent_id=_agent_os_source_id,
        payload={
            "run_id": run["run_id"],
            "profile_name": profile_name,
            "requested_model": model_name,
            "session_id": str(session_id or ""),
            "streaming": False,
        },
    )
    try:
        plan = planner.plan(planner_input)
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = plan.get("route", "chat")
        temporal = plan.get("temporal", {})
        web_plan = plan.get("web_plan", {"is_multi_intent": False, "subqueries": []})
        effective_model = pick_model_for_route(route, model_name)
        selected = [
            t for t in plan.get("tools", [])
            if not (t == "memory_search" and not use_memory)
            and not (t == "library_context" and not use_library)
            and not (t == "web_search" and not use_web_search)
        ]
        if temporal.get("requires_web") and use_web_search and "web_search" not in selected:
            selected.append("web_search")
        strict_web_only = (
            route == "research"
            and temporal.get("mode") == "hard"
            and temporal.get("freshness_sensitive")
        )
        if strict_web_only:
            selected = [t for t in selected if t != "memory_search"]
        if is_memory_command(planner_input):
            selected = [t for t in selected if t != "memory_search"]

        try:
            saved = extract_and_save(planner_input)
            if saved:
                _tl(timeline, "memory_save", "Память", "done", "Сохранено: " + str(len(saved)))
        except Exception:
            pass

        preflight_or_raise(
            agent_id=_effective_agent_id,
            num_ctx=num_ctx,
            selected_tools=selected,
            run_id=run["run_id"],
            route=route,
            streaming=False,
        )

        ctx = _collect_context(
            profile_name=profile_name,
            user_input=planner_input,
            tools=selected,
            tool_results=tool_results,
            timeline=timeline,
            use_reflection=use_reflection,
            temporal=temporal,
            web_plan=web_plan,
        )

        if _should_recall_memory_context(planner_input, route, temporal):
            try:
                mem_limit, rag_limit = _get_memory_recall_limits(planner_input)
                mem_ctx = get_relevant_context(planner_input, max_items=mem_limit)
                if _HAS_RAG and rag_limit > 0:
                    rag_ctx = get_rag_context(planner_input, max_items=rag_limit)
                    if rag_ctx:
                        mem_ctx = (mem_ctx + "\n\n" + rag_ctx) if mem_ctx else rag_ctx
                if mem_ctx:
                    ctx = mem_ctx + "\n\n" + ctx if ctx else mem_ctx
                    _tl(timeline, "memory_recall", "Память", "done", "Найдены релевантные заметки")
            except Exception:
                pass

        prompt = (
            _build_prompt(raw_user_input, ctx, disabled_skills=_disabled_skills)
            + _compose_human_style_rules(temporal)
        )
        task_context = (
            f"Маршрут: {route}. Инструменты: "
            + (", ".join(selected) if selected else "нет дополнительных инструментов")
            + "."
        )
        draft = run_chat(
            model_name=effective_model,
            profile_name=profile_name,
            user_input=prompt,
            history=history,
            num_ctx=num_ctx,
            task_context=task_context,
        )
        if not draft.get("ok"):
            raise RuntimeError("; ".join(draft.get("warnings", [])) or "LLM failed")
        answer = draft.get("answer", "")

        has_generated_files = any(a["type"] in ("image", "file") for a in _pending_attachments)
        should_reflect = (route in _REFLECTION_ROUTES) or use_reflection
        if should_reflect and answer.strip() and not has_generated_files:
            ref = run_reflection_loop(
                model_name=effective_model,
                profile_name=profile_name,
                user_input=raw_user_input,
                draft_text=answer,
                review_text="Улучши.",
                context=ctx,
            )
            answer = ref.get("answer") or answer

        attachments = _get_and_clear_attachments()
        if attachments:
            answer += attachments

        post_files = _maybe_generate_files(raw_user_input, answer, enabled=use_file_gen)
        if post_files:
            answer += post_files

        identity_guard = _apply_identity_guard(raw_user_input, answer, timeline)
        answer = identity_guard.get("text", answer)
        provenance_guard = _apply_provenance_guard(raw_user_input, answer, timeline)
        answer = provenance_guard.get("text", answer)

        persona_meta = observe_dialogue(
            dialog_id=run["run_id"],
            session_id=str(session_id or run["run_id"]),
            profile_name=profile_name,
            model_name=effective_model,
            user_input=raw_user_input,
            answer_text=answer,
            route=route,
            outcome_ok=True,
        )
        result = {
            "ok": True,
            "answer": answer,
            "timeline": timeline,
            "tool_results": tool_results,
            "meta": {
                "model_name": effective_model,
                "profile_name": profile_name,
                "route": route,
                "tools": selected,
                "run_id": run["run_id"],
                "persona": persona_meta,
                "temporal": temporal,
                "web_plan": web_plan,
                "identity_guard": identity_guard if identity_guard.get("changed") else None,
                "provenance_guard": provenance_guard if provenance_guard.get("changed") else None,
            },
        }
        _HISTORY.finish_run(run["run_id"], result)
        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
        _record_agent_os_monitoring(
            agent_id=_effective_agent_id,
            run_id=run["run_id"],
            route=route,
            model_name=effective_model,
            ok=True,
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            selected_tools=selected,
        )

        if agent_id or _registry_agent:
            try:
                record_agent_run({
                    "agent_id": agent_id or (_registry_agent or {}).get("id", ""),
                    "run_id": run["run_id"],
                    "input_summary": raw_user_input[:500],
                    "output_summary": answer[:500],
                    "ok": True,
                    "route": route,
                    "model_used": effective_model,
                    "duration_ms": _duration_ms,
                })
            except Exception:
                pass

        _emit_agent_os_event(
            event_type="agent.run.completed",
            source_agent_id=_agent_os_source_id,
            payload={
                "run_id": run["run_id"],
                "profile_name": profile_name,
                "route": route,
                "ok": True,
                "model_used": effective_model,
                "duration_ms": _duration_ms,
                "session_id": str(session_id or ""),
                "streaming": False,
            },
        )
        return result

    except SandboxPolicyError as exc:
        err = {
            "ok": False,
            "answer": "",
            "timeline": timeline + [{"step": "sandbox", "title": "Sandbox", "status": "error", "detail": str(exc)}],
            "tool_results": tool_results,
            "meta": {
                "error": str(exc),
                "run_id": run["run_id"],
                "sandbox_reason": exc.reason,
                "sandbox_details": exc.details,
            },
        }
        _HISTORY.finish_run(run["run_id"], err)
        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
        _record_agent_os_monitoring(
            agent_id=_effective_agent_id,
            run_id=run["run_id"],
            route=locals().get("route", ""),
            model_name=locals().get("effective_model", model_name),
            ok=False,
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            selected_tools=locals().get("selected", []),
        )
        _emit_agent_os_event(
            event_type="agent.run.completed",
            source_agent_id=_agent_os_source_id,
            payload={
                "run_id": run["run_id"],
                "profile_name": profile_name,
                "route": locals().get("route", ""),
                "ok": False,
                "model_used": locals().get("effective_model", model_name),
                "duration_ms": _duration_ms,
                "error": str(exc)[:500],
                "session_id": str(session_id or ""),
                "streaming": False,
            },
        )
        return err

    except Exception as exc:
        err = {
            "ok": False,
            "answer": "",
            "timeline": timeline + [{"step": "error", "title": "Ошибка", "status": "error", "detail": str(exc)}],
            "tool_results": tool_results,
            "meta": {"error": str(exc), "run_id": run["run_id"]},
        }
        _HISTORY.finish_run(run["run_id"], err)
        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
        _record_agent_os_monitoring(
            agent_id=_effective_agent_id,
            run_id=run["run_id"],
            route=locals().get("route", ""),
            model_name=locals().get("effective_model", model_name),
            ok=False,
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            selected_tools=locals().get("selected", []),
        )
        if agent_id or _registry_agent:
            try:
                record_agent_run({
                    "agent_id": agent_id or (_registry_agent or {}).get("id", ""),
                    "run_id": run["run_id"],
                    "input_summary": (raw_user_input if "raw_user_input" in dir() else user_input)[:500],
                    "output_summary": str(exc)[:500],
                    "ok": False,
                    "route": "",
                    "model_used": model_name,
                    "duration_ms": _duration_ms,
                })
            except Exception:
                pass
        _emit_agent_os_event(
            event_type="agent.run.completed",
            source_agent_id=_agent_os_source_id,
            payload={
                "run_id": run["run_id"],
                "profile_name": profile_name,
                "route": locals().get("route", ""),
                "ok": False,
                "model_used": locals().get("effective_model", model_name),
                "duration_ms": _duration_ms,
                "error": str(exc)[:500],
                "session_id": str(session_id or ""),
                "streaming": False,
            },
        )
        return err
