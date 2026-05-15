"""Chat stream response helpers + streaming agent execution.

Extracted from agents_service.py.  Shared helpers are imported from
service.py to avoid duplication.
"""
from __future__ import annotations

import time as _time
from typing import Any, Generator, Iterator

from app.application.chat.auto_skills import (
    _FILE_TRIGGERS_EXCEL,
    _FILE_TRIGGERS_WORD,
    _build_prompt,
    _get_and_clear_attachments,
    _maybe_auto_exec_python,
    _maybe_generate_files,
    _pending_attachments,
)
from app.application.chat.prompt_builder import (
    compose_human_style_rules as _compose_human_style_rules,
)
from app.application.chat.service import (
    _HISTORY,
    _HAS_RAG,
    _REFLECTION_ROUTES,
    _apply_identity_guard,
    _apply_provenance_guard,
    _collect_context,
    _emit_agent_os_event,
    _get_memory_recall_limits,
    _record_agent_os_monitoring,
    _should_recall_memory_context,
    _strip_frontend_project_context,
    _tl,
    _trim_history,
    get_rag_context,
)
from app.core.config import pick_model_for_route
from app.application.monitoring.agent_sandbox import preflight_or_raise, resolve_effective_agent_id
from app.application.chat.chat_service import run_chat_stream
from app.application.persona.persona_service import observe_dialogue
from app.application.planning.planner_v2_service import PlannerV2Service
from app.application.agents.reflection_loop_service import run_reflection_loop
from app.infrastructure.cache.response_cache import get_cached, set_cached, should_cache
from app.application.memory.smart_memory import extract_and_save, get_relevant_context, is_memory_command


# ─── stream meta helpers (kept for backward compatibility) ───────────────────


def build_chat_meta(
    *,
    model_name: str,
    profile_name: str,
    route: str,
    tools: list[str],
    run_id: str,
    temporal: dict[str, Any],
    web_plan: dict[str, Any],
    persona: dict[str, Any] | None = None,
    identity_guard: dict[str, Any] | None = None,
    provenance_guard: dict[str, Any] | None = None,
    cached: bool = False,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "model_name": model_name,
        "profile_name": profile_name,
        "route": route,
        "tools": tools,
        "run_id": run_id,
        "temporal": temporal,
        "web_plan": web_plan,
        "identity_guard": identity_guard if (identity_guard or {}).get("changed") else None,
        "provenance_guard": provenance_guard if (provenance_guard or {}).get("changed") else None,
    }
    if persona is not None:
        meta["persona"] = persona
    if cached:
        meta["cached"] = True
    return meta


def iter_text_stream_events(text: str) -> Iterator[dict[str, Any]]:
    words = text.split(" ")
    for index, word in enumerate(words):
        yield {"token": word if index == 0 else " " + word, "done": False}


def build_stream_done_event(
    *,
    full_text: str,
    meta: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    return {"token": "", "done": True, "full_text": full_text, "meta": meta, "timeline": timeline}


# ─── execute_chat_agent_stream (streaming) ───────────────────────────────────


def execute_chat_agent_stream(
    *,
    model_name,
    profile_name,
    user_input,
    session_id=None,
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
) -> Generator[dict[str, Any], None, None]:
    _agent_start = _time.monotonic()
    _effective_agent_id = resolve_effective_agent_id(profile_name=profile_name)
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
    _emit_agent_os_event(
        event_type="agent.run.started",
        source_agent_id=_effective_agent_id,
        payload={
            "run_id": run["run_id"],
            "profile_name": profile_name,
            "requested_model": model_name,
            "session_id": str(session_id or ""),
            "streaming": True,
        },
    )
    try:
        yield {"token": "", "done": False, "phase": "planning", "message": "Думаю..."}

        plan = planner.plan(planner_input)
        _HISTORY.add_event(run["run_id"], "planner", plan)
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
        preflight_or_raise(
            agent_id=_effective_agent_id,
            num_ctx=num_ctx,
            selected_tools=selected,
            run_id=run["run_id"],
            route=route,
            streaming=True,
        )
        if effective_model != model_name:
            _tl(timeline, "auto_model", "Авто-модель", "ok",
                f"{model_name} → {effective_model} (route={route})")

        if should_cache(planner_input, route) and not history:
            cached = get_cached(planner_input, effective_model, profile_name)
            if cached:
                _tl(timeline, "cache_hit", "Кэш", "ok", "Ответ из кэша")
                identity_guard = _apply_identity_guard(raw_user_input, cached, timeline)
                cached = identity_guard.get("text", cached)
                provenance_guard = _apply_provenance_guard(raw_user_input, cached, timeline)
                cached = provenance_guard.get("text", cached)
                meta = {
                    "model_name": effective_model,
                    "profile_name": profile_name,
                    "route": route,
                    "tools": [],
                    "run_id": run["run_id"],
                    "cached": True,
                    "temporal": temporal,
                    "web_plan": web_plan,
                    "identity_guard": identity_guard if identity_guard.get("changed") else None,
                    "provenance_guard": provenance_guard if provenance_guard.get("changed") else None,
                }
                persona_meta = observe_dialogue(
                    dialog_id=run["run_id"],
                    session_id=str(session_id or run["run_id"]),
                    profile_name=profile_name,
                    model_name=effective_model,
                    user_input=raw_user_input,
                    answer_text=cached,
                    route=route,
                    outcome_ok=True,
                )
                meta["persona"] = persona_meta
                _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": cached, "meta": meta})
                _record_agent_os_monitoring(
                    agent_id=_effective_agent_id,
                    run_id=run["run_id"],
                    route=route,
                    model_name=effective_model,
                    ok=True,
                    duration_ms=int((_time.monotonic() - _agent_start) * 1000),
                    streaming=True,
                    num_ctx=num_ctx,
                    selected_tools=selected,
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
                        "duration_ms": int((_time.monotonic() - _agent_start) * 1000),
                        "session_id": str(session_id or ""),
                        "streaming": True,
                    },
                )
                words = cached.split(" ")
                for i, word in enumerate(words):
                    yield {"token": word if i == 0 else " " + word, "done": False}
                yield {"token": "", "done": True, "full_text": cached, "meta": meta, "timeline": timeline}
                return

        try:
            extract_and_save(planner_input)
        except Exception:
            pass

        if "web_search" in selected:
            yield {"token": "", "done": False, "phase": "searching", "message": "Ищу..."}
        elif selected:
            yield {"token": "", "done": False, "phase": "tools", "message": "Собираю контекст..."}

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
            except Exception:
                pass

        yield {"token": "", "done": False, "phase": "thinking", "message": "Пишу ответ..."}

        prompt = (
            _build_prompt(raw_user_input, ctx, disabled_skills=_disabled_skills)
            + _compose_human_style_rules(temporal)
        )
        full_text = ""
        task_context = (
            f"Маршрут: {route}. Инструменты: "
            + (", ".join(selected) if selected else "нет дополнительных инструментов")
            + "."
        )
        for token in run_chat_stream(
            model_name=effective_model,
            profile_name=profile_name,
            user_input=prompt,
            history=history,
            num_ctx=num_ctx,
            task_context=task_context,
        ):
            full_text += token
            yield {"token": token, "done": False}

        attachments = _get_and_clear_attachments()
        if attachments:
            full_text += attachments

        has_generated_files = any(a["type"] in ("image", "file") for a in _pending_attachments)
        should_reflect = (route in _REFLECTION_ROUTES) or use_reflection
        ql_check = raw_user_input.lower()
        needs_file_gen = any(t in ql_check for t in _FILE_TRIGGERS_WORD + _FILE_TRIGGERS_EXCEL)

        if not should_reflect and not needs_file_gen:
            try:
                full_text = _maybe_auto_exec_python(raw_user_input, full_text, timeline, enabled=use_python_exec)
            except Exception:
                pass
            post_files = _maybe_generate_files(raw_user_input, full_text, enabled=use_file_gen)
            if post_files:
                full_text += post_files
            identity_guard = _apply_identity_guard(raw_user_input, full_text, timeline)
            guarded_text = identity_guard.get("text", full_text)
            provenance_guard = _apply_provenance_guard(raw_user_input, guarded_text, timeline)
            guarded_text = provenance_guard.get("text", guarded_text)
            if guarded_text != full_text:
                full_text = guarded_text
                yield {"token": "", "done": False, "phase": "reflection_replace", "full_text": full_text}
            if should_cache(planner_input, route) and full_text.strip():
                try:
                    set_cached(planner_input, effective_model, profile_name, full_text)
                except Exception:
                    pass
            persona_meta = observe_dialogue(
                dialog_id=run["run_id"],
                session_id=str(session_id or run["run_id"]),
                profile_name=profile_name,
                model_name=effective_model,
                user_input=raw_user_input,
                answer_text=full_text,
                route=route,
                outcome_ok=True,
            )
            meta = {
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
            }
            _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": full_text, "meta": meta})
            _record_agent_os_monitoring(
                agent_id=_effective_agent_id,
                run_id=run["run_id"],
                route=route,
                model_name=effective_model,
                ok=True,
                duration_ms=int((_time.monotonic() - _agent_start) * 1000),
                streaming=True,
                num_ctx=num_ctx,
                selected_tools=selected,
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
                    "duration_ms": int((_time.monotonic() - _agent_start) * 1000),
                    "session_id": str(session_id or ""),
                    "streaming": True,
                },
            )
            yield {"token": "", "done": True, "full_text": full_text, "meta": meta, "timeline": timeline}
        else:
            if should_reflect and full_text.strip() and not has_generated_files:
                yield {"token": "", "done": False, "phase": "reflecting", "message": "Проверяю..."}
                try:
                    ref = run_reflection_loop(
                        model_name=effective_model,
                        profile_name=profile_name,
                        user_input=raw_user_input,
                        draft_text=full_text,
                        review_text="Улучши.",
                        context=ctx,
                    )
                    refined = ref.get("answer", "")
                    if refined and refined != full_text:
                        full_text = refined
                        yield {"token": "", "done": False, "phase": "reflection_replace", "full_text": refined}
                except Exception:
                    pass

            try:
                full_text = _maybe_auto_exec_python(raw_user_input, full_text, timeline, enabled=use_python_exec)
            except Exception:
                pass

            if needs_file_gen:
                yield {"token": "", "done": False, "phase": "generating_file", "message": "Готовлю файл..."}
            post_files = _maybe_generate_files(raw_user_input, full_text, enabled=use_file_gen)
            if post_files:
                full_text += post_files

            identity_guard = _apply_identity_guard(raw_user_input, full_text, timeline)
            guarded_text = identity_guard.get("text", full_text)
            provenance_guard = _apply_provenance_guard(raw_user_input, guarded_text, timeline)
            guarded_text = provenance_guard.get("text", guarded_text)
            if guarded_text != full_text:
                full_text = guarded_text
                yield {"token": "", "done": False, "phase": "reflection_replace", "full_text": full_text}

            if should_cache(planner_input, route) and full_text.strip():
                try:
                    set_cached(planner_input, effective_model, profile_name, full_text)
                except Exception:
                    pass

            persona_meta = observe_dialogue(
                dialog_id=run["run_id"],
                session_id=str(session_id or run["run_id"]),
                profile_name=profile_name,
                model_name=effective_model,
                user_input=raw_user_input,
                answer_text=full_text,
                route=route,
                outcome_ok=True,
            )
            meta = {
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
            }
            _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": full_text, "meta": meta})
            _record_agent_os_monitoring(
                agent_id=_effective_agent_id,
                run_id=run["run_id"],
                route=route,
                model_name=effective_model,
                ok=True,
                duration_ms=int((_time.monotonic() - _agent_start) * 1000),
                streaming=True,
                num_ctx=num_ctx,
                selected_tools=selected,
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
                    "duration_ms": int((_time.monotonic() - _agent_start) * 1000),
                    "session_id": str(session_id or ""),
                    "streaming": True,
                },
            )
            yield {"token": "", "done": True, "full_text": full_text, "meta": meta, "timeline": timeline}

    except Exception as exc:
        _HISTORY.finish_run(run["run_id"], {"ok": False, "error": str(exc)})
        _record_agent_os_monitoring(
            agent_id=_effective_agent_id,
            run_id=run["run_id"],
            route=locals().get("route", ""),
            model_name=locals().get("effective_model", model_name),
            ok=False,
            duration_ms=int((_time.monotonic() - _agent_start) * 1000),
            streaming=True,
            num_ctx=num_ctx,
            selected_tools=locals().get("selected", []),
        )
        _emit_agent_os_event(
            event_type="agent.run.completed",
            source_agent_id=_effective_agent_id,
            payload={
                "run_id": run["run_id"],
                "profile_name": profile_name,
                "route": locals().get("route", ""),
                "ok": False,
                "model_used": locals().get("effective_model", model_name),
                "duration_ms": int((_time.monotonic() - _agent_start) * 1000),
                "error": str(exc)[:500],
                "session_id": str(session_id or ""),
                "streaming": True,
            },
        )
        yield {"token": "", "done": True, "error": str(exc), "full_text": ""}
