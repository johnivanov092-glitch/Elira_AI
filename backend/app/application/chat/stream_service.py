"""Chat stream response helpers + streaming agent execution.

Extracted from agents_service.py.  Shared helpers are imported from
service.py to avoid duplication.
"""
from __future__ import annotations

import time as _time
from typing import Any, Generator

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
    _resolve_plan_and_tools,
    _should_recall_memory_context,
    _strip_frontend_project_context,
    _tl,
    _trim_history,
    get_rag_context,
)
from app.application.monitoring.agent_sandbox import preflight_or_raise, resolve_effective_agent_id
from app.application.chat.chat_service import run_chat_stream
from app.application.persona.persona_service import observe_dialogue
from app.application.agents.reflection_loop_service import run_reflection_loop
from app.infrastructure.cache.response_cache import get_cached, set_cached, should_cache
from app.application.memory.smart_memory import extract_and_save, get_relevant_context, is_memory_command



def _apply_guards_and_complete_stream(
    *,
    raw_user_input: str,
    full_text: str,
    planner_input: str,
    effective_model: str,
    profile_name: str,
    route: str,
    selected: list,
    run: dict,
    session_id,
    timeline: list,
    _effective_agent_id: str,
    _agent_start: float,
    num_ctx: int,
    temporal: dict,
    web_plan: dict,
) -> tuple[str, dict, bool]:
    """Apply guards, cache, persona, finish run; emit completion events.

    Returns ``(full_text, meta, guard_changed)``. If *guard_changed* is True
    the caller should yield a ``reflection_replace`` event before ``done``.
    """
    identity_guard = _apply_identity_guard(raw_user_input, full_text, timeline)
    guarded_text = identity_guard.get("text", full_text)
    provenance_guard = _apply_provenance_guard(raw_user_input, guarded_text, timeline)
    guarded_text = provenance_guard.get("text", guarded_text)
    guard_changed = guarded_text != full_text
    if guard_changed:
        full_text = guarded_text
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
    return full_text, meta, guard_changed


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

        route, temporal, web_plan, selected, effective_model = _resolve_plan_and_tools(
            planner_input, run["run_id"], model_name,
            use_memory=use_memory, use_library=use_library, use_web_search=use_web_search,
        )
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
            full_text, meta, guard_changed = _apply_guards_and_complete_stream(
                raw_user_input=raw_user_input,
                full_text=full_text,
                planner_input=planner_input,
                effective_model=effective_model,
                profile_name=profile_name,
                route=route,
                selected=selected,
                run=run,
                session_id=session_id,
                timeline=timeline,
                _effective_agent_id=_effective_agent_id,
                _agent_start=_agent_start,
                num_ctx=num_ctx,
                temporal=temporal,
                web_plan=web_plan,
            )
            if guard_changed:
                yield {"token": "", "done": False, "phase": "reflection_replace", "full_text": full_text}
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

            full_text, meta, guard_changed = _apply_guards_and_complete_stream(
                raw_user_input=raw_user_input,
                full_text=full_text,
                planner_input=planner_input,
                effective_model=effective_model,
                profile_name=profile_name,
                route=route,
                selected=selected,
                run=run,
                session_id=session_id,
                timeline=timeline,
                _effective_agent_id=_effective_agent_id,
                _agent_start=_agent_start,
                num_ctx=num_ctx,
                temporal=temporal,
                web_plan=web_plan,
            )
            if guard_changed:
                yield {"token": "", "done": False, "phase": "reflection_replace", "full_text": full_text}
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
