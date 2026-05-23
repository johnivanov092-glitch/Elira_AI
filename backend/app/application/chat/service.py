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


def _gather_run_context(
    *,
    profile_name: str,
    planner_input: str,
    route: str,
    temporal: dict,
    selected: list,
    tool_results: list,
    timeline: list,
    use_reflection: bool,
    web_plan: dict,
) -> str:
    """Collect tool / RAG context then augment with memory recall if applicable."""
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
        except Exception as _exc:
            logger.debug("memory_recall failed: %s", _exc)
    return ctx


def _run_llm_and_finish_answer(
    *,
    raw_user_input: str,
    prompt: str,
    history: list,
    effective_model: str,
    profile_name: str,
    num_ctx: int,
    task_context: str,
    route: str,
    use_reflection: bool,
    ctx: str,
    use_file_gen: bool,
) -> str:
    """Call LLM, optionally run reflection, apply attachments and file generation.

    Returns the final answer string.  Raises RuntimeError on LLM failure so the
    caller's except handlers can handle it uniformly.
    """
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

    return answer


def _setup_chat_agent_run(
    *,
    user_input: str,
    profile_name: str,
    model_name: str,
    session_id,
    history,
    streaming: bool,
    agent_id=None,
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
) -> tuple:
    """Resolve registry agent, build skill flags, start run history, emit started event.

    Returns (_agent_start, _registry_agent, _effective_agent_id, profile_name, model_name,
             history, _disabled_skills, timeline, tool_results, raw_user_input, planner_input, run).
    profile_name and model_name may be overridden by the registry agent's preferences.
    """
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
        except Exception as _exc:
            logger.debug("registry_agent resolve failed: %s", _exc)

    _effective_agent_id = resolve_effective_agent_id(
        agent_id=agent_id, profile_name=profile_name, registry_agent=_registry_agent,
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
    raw_user_input = user_input
    planner_input = _strip_frontend_project_context(user_input)
    run = _HISTORY.start_run(raw_user_input)
    _emit_agent_os_event(
        event_type="agent.run.started",
        source_agent_id=_effective_agent_id,
        payload={
            "run_id": run["run_id"],
            "profile_name": profile_name,
            "requested_model": model_name,
            "session_id": str(session_id or ""),
            "streaming": streaming,
        },
    )
    return (
        _agent_start, _registry_agent, _effective_agent_id, profile_name, model_name,
        history, _disabled_skills, timeline, tool_results, raw_user_input, planner_input, run,
    )


def _build_run_result(
    *,
    raw_user_input: str,
    answer: str,
    timeline: list,
    tool_results: list,
    run: dict,
    session_id,
    profile_name: str,
    effective_model: str,
    route: str,
    selected: list,
    temporal: dict,
    web_plan: dict,
) -> tuple[str, dict]:
    """Apply identity/provenance guards, observe persona, assemble result dict.

    Returns ``(guarded_answer, result_dict)``.
    """
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
    return answer, result


def _maybe_record_agent_run(
    *,
    agent_id,
    _registry_agent,
    run_id: str,
    raw_user_input: str,
    output_summary: str,
    route: str,
    effective_model: str,
    duration_ms: int,
    ok: bool = True,
) -> None:
    """Record a registry-agent run if an agent_id or registry_agent is present."""
    if not (agent_id or _registry_agent):
        return
    try:
        record_agent_run({
            "agent_id": agent_id or (_registry_agent or {}).get("id", ""),
            "run_id": run_id,
            "input_summary": raw_user_input[:500],
            "output_summary": output_summary[:500],
            "ok": ok,
            "route": route,
            "model_used": effective_model,
            "duration_ms": duration_ms,
        })
    except Exception as _exc:
        logger.debug("record_agent_run failed: %s", _exc)


def _complete_chat_run(
    *,
    raw_user_input: str,
    answer: str,
    timeline: list,
    tool_results: list,
    run: dict,
    session_id,
    profile_name: str,
    effective_model: str,
    route: str,
    selected: list,
    temporal: dict,
    web_plan: dict,
    _effective_agent_id: str,
    _agent_start: float,
    num_ctx: int,
    agent_id,
    _registry_agent,
) -> dict:
    """Apply guards, build result, finish run, emit completion events."""
    answer, result = _build_run_result(
        raw_user_input=raw_user_input, answer=answer, timeline=timeline,
        tool_results=tool_results, run=run, session_id=session_id,
        profile_name=profile_name, effective_model=effective_model,
        route=route, selected=selected, temporal=temporal, web_plan=web_plan,
    )
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
    _maybe_record_agent_run(
        agent_id=agent_id, _registry_agent=_registry_agent,
        run_id=run["run_id"], raw_user_input=raw_user_input,
        output_summary=answer, route=route,
        effective_model=effective_model, duration_ms=_duration_ms,
    )
    _emit_agent_os_event(
        event_type="agent.run.completed",
        source_agent_id=_effective_agent_id,
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


def _resolve_plan_and_tools(
    planner_input: str,
    run_id: str,
    model_name: str,
    *,
    use_memory: bool,
    use_library: bool,
    use_web_search: bool,
) -> tuple[str, dict, dict, list[str], str]:
    """Run planner, filter tools by capability flags, pick effective model.

    Returns (route, temporal, web_plan, selected_tools, effective_model).
    """
    plan = PlannerV2Service().plan(planner_input)
    _HISTORY.add_event(run_id, "planner", plan)
    route = plan.get("route", "chat")
    temporal = plan.get("temporal", {})
    web_plan = plan.get("web_plan", {"is_multi_intent": False, "subqueries": []})
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
    effective_model = pick_model_for_route(route, model_name)
    return route, temporal, web_plan, selected, effective_model


def _handle_chat_run_failure(
    *,
    exc: Exception,
    run: dict,
    timeline: list,
    tool_results: list,
    _effective_agent_id: str,
    _agent_os_source_id: str,
    _agent_start: float,
    model_name: str,
    profile_name: str,
    session_id,
    num_ctx: int,
    streaming: bool,
    tl_step: dict,
    extra_meta: dict | None = None,
    agent_id: str | None = None,
    _registry_agent: dict | None = None,
    route: str = "",
    effective_model: str | None = None,
    selected: list | None = None,
    raw_user_input: str = "",
) -> dict:
    """Build error result dict, finish run, record monitoring and emit completion event.

    tl_step    – timeline entry to append (e.g. {"step": "sandbox", ...})
    extra_meta – additional keys merged into meta (sandbox_reason / sandbox_details)
    """
    meta: dict = {"error": str(exc), "run_id": run["run_id"]}
    if extra_meta:
        meta.update(extra_meta)
    err = {
        "ok": False,
        "answer": "",
        "timeline": list(timeline) + [tl_step],
        "tool_results": tool_results,
        "meta": meta,
    }
    _HISTORY.finish_run(run["run_id"], err)
    _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
    _record_agent_os_monitoring(
        agent_id=_effective_agent_id,
        run_id=run["run_id"],
        route=route,
        model_name=effective_model or model_name,
        ok=False,
        duration_ms=_duration_ms,
        streaming=streaming,
        num_ctx=num_ctx,
        selected_tools=selected or [],
    )
    _maybe_record_agent_run(
        agent_id=agent_id, _registry_agent=_registry_agent,
        run_id=run["run_id"], raw_user_input=raw_user_input,
        output_summary=str(exc), route=route,
        effective_model=effective_model or model_name, duration_ms=_duration_ms,
        ok=False,
    )
    _emit_agent_os_event(
        event_type="agent.run.completed",
        source_agent_id=_agent_os_source_id,
        payload={
            "run_id": run["run_id"],
            "profile_name": profile_name,
            "route": route,
            "ok": False,
            "model_used": effective_model or model_name,
            "duration_ms": _duration_ms,
            "error": str(exc)[:500],
            "session_id": str(session_id or ""),
            "streaming": streaming,
        },
    )
    return err


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
    (
        _agent_start, _registry_agent, _effective_agent_id, profile_name, model_name,
        history, _disabled_skills, timeline, tool_results, raw_user_input, planner_input, run,
    ) = _setup_chat_agent_run(
        user_input=user_input, profile_name=profile_name, model_name=model_name,
        session_id=session_id, history=history, streaming=False, agent_id=agent_id,
        use_web_search=use_web_search, use_python_exec=use_python_exec,
        use_image_gen=use_image_gen, use_file_gen=use_file_gen,
        use_http_api=use_http_api, use_sql=use_sql, use_screenshot=use_screenshot,
        use_encrypt=use_encrypt, use_archiver=use_archiver, use_converter=use_converter,
        use_regex=use_regex, use_translator=use_translator, use_csv=use_csv,
        use_webhook=use_webhook, use_plugins=use_plugins,
    )
    _agent_os_source_id = _effective_agent_id
    route, temporal, web_plan, selected, effective_model = "", {}, {}, [], model_name
    try:
        route, temporal, web_plan, selected, effective_model = _resolve_plan_and_tools(
            planner_input, run["run_id"], model_name,
            use_memory=use_memory, use_library=use_library, use_web_search=use_web_search,
        )

        try:
            saved = extract_and_save(planner_input)
            if saved:
                _tl(timeline, "memory_save", "Память", "done", "Сохранено: " + str(len(saved)))
        except Exception as _exc:
            logger.debug("memory_save failed: %s", _exc)

        preflight_or_raise(
            agent_id=_effective_agent_id,
            num_ctx=num_ctx,
            selected_tools=selected,
            run_id=run["run_id"],
            route=route,
            streaming=False,
        )

        ctx = _gather_run_context(
            profile_name=profile_name, planner_input=planner_input,
            route=route, temporal=temporal, selected=selected,
            tool_results=tool_results, timeline=timeline,
            use_reflection=use_reflection, web_plan=web_plan,
        )

        prompt = (
            _build_prompt(raw_user_input, ctx, disabled_skills=_disabled_skills)
            + _compose_human_style_rules(temporal)
        )
        task_context = (
            f"Маршрут: {route}. Инструменты: "
            + (", ".join(selected) if selected else "нет дополнительных инструментов")
            + "."
        )
        answer = _run_llm_and_finish_answer(
            raw_user_input=raw_user_input, prompt=prompt,
            history=history, effective_model=effective_model,
            profile_name=profile_name, num_ctx=num_ctx,
            task_context=task_context, route=route,
            use_reflection=use_reflection, ctx=ctx, use_file_gen=use_file_gen,
        )

        return _complete_chat_run(
            raw_user_input=raw_user_input,
            answer=answer,
            timeline=timeline,
            tool_results=tool_results,
            run=run,
            session_id=session_id,
            profile_name=profile_name,
            effective_model=effective_model,
            route=route,
            selected=selected,
            temporal=temporal,
            web_plan=web_plan,
            _effective_agent_id=_effective_agent_id,
            _agent_start=_agent_start,
            num_ctx=num_ctx,
            agent_id=agent_id,
            _registry_agent=_registry_agent,
        )

    except Exception as exc:
        _is_sandbox = isinstance(exc, SandboxPolicyError)
        return _handle_chat_run_failure(
            exc=exc, run=run, timeline=timeline, tool_results=tool_results,
            _effective_agent_id=_effective_agent_id, _agent_os_source_id=_agent_os_source_id,
            _agent_start=_agent_start, model_name=model_name, profile_name=profile_name,
            session_id=session_id, num_ctx=num_ctx, streaming=False,
            tl_step={"step": "sandbox" if _is_sandbox else "error",
                     "title": "Sandbox" if _is_sandbox else "Ошибка",
                     "status": "error", "detail": str(exc)},
            extra_meta={"sandbox_reason": exc.reason, "sandbox_details": exc.details} if _is_sandbox else None,
            agent_id=None if _is_sandbox else agent_id,
            _registry_agent=None if _is_sandbox else _registry_agent,
            route=route, effective_model=effective_model, selected=selected,
            raw_user_input=raw_user_input,
        )
