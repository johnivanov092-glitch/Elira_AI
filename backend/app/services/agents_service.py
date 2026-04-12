"""
agents_service.py v8

Улучшения v8:
  • Авто-выбор модели под задачу (route → лучшая модель)
  • Кэширование ответов (SQLite, TTL 2 часа)
  • Умная обрезка истории (релевантные сообщения, не просто последние N)
  • Детальные фазы стриминга
"""
# Legacy monolith: keep behavior stable and prefer extraction into
# application/domain/infrastructure modules over new feature work here.
from __future__ import annotations

from functools import partial
from typing import Any

from app.application.chat.context_builder import (
    collect_context as build_chat_context,
    strip_frontend_project_context as build_strip_frontend_project_context,
)
from app.application.chat.service import (
    bootstrap_chat_run,
    build_task_context,
    prepare_chat_execution,
    prepare_chat_prompt,
)
from app.application.chat.prompting import (
    build_prompt as _app_build_prompt,
    compose_human_style_rules as _app_compose_human_style_rules,
    get_and_clear_attachments as _app_get_and_clear_attachments,
    has_generated_file_attachments as _app_has_generated_file_attachments,
)
from app.application.chat.memory_policy import (
    enrich_context_with_memory as _app_enrich_context_with_memory,
    trim_history as _app_trim_history,
)
from app.application.chat.post_processing import (
    apply_identity_guard as _app_apply_identity_guard,
    apply_provenance_guard as _app_apply_provenance_guard,
    apply_response_guards,
)
from app.application.chat.agent_os import (
    emit_agent_os_event as _app_emit_agent_os_event,
    record_registry_agent_run as _app_record_registry_agent_run,
)
from app.application.chat.finalization import (
    finalize_chat_failure as _app_finalize_chat_failure,
    finalize_chat_success as _app_finalize_chat_success,
    finalize_stream_success as _app_finalize_stream_success,
)
from app.application.chat.stream_service import (
    build_selected_tools_phase_event,
    build_stream_phase_event,
    finalize_stream_response,
    iter_text_stream_events,
    prepare_cached_stream_hit,
)
from app.infrastructure.search.web_search import do_temporal_web_search as _infra_do_temporal_web_search
from app.services.agent_sandbox import (
    SandboxPolicyError,
    preflight_or_raise,
    resolve_effective_agent_id,
)
from app.services.chat_service import run_chat, run_chat_stream
from app.services.planner_v2_service import PlannerV2Service
from app.services.reflection_loop_service import run_reflection_loop
from app.services.run_history_service import RunHistoryService
from app.services.tool_service import run_tool
from app.services.smart_memory import extract_and_save, get_relevant_context, is_memory_command
from app.services.response_cache import get_cached, set_cached, should_cache
from app.core.config import pick_model_for_route

# RAG память (опционально — если embedding модель доступна)
try:
    from app.services.rag_memory_service import get_rag_context, add_to_rag
    _HAS_RAG = True
except ImportError:
    _HAS_RAG = False
    def get_rag_context(*a, **kw): return ""
    def add_to_rag(*a, **kw): return {}

_HISTORY = RunHistoryService()
_REFLECTION_ROUTES = {"code", "project"}
_MAX_HISTORY_PAIRS = 10


def _tl(timeline, step, title, status, detail):
    timeline.append({"step": step, "title": title, "status": status, "detail": detail})


# ═══════════════════════════════════════════════════════════════
# POST-ГЕНЕРАЦИЯ ФАЙЛОВ: LLM написал ответ → сохраняем в Word/Excel
# ═══════════════════════════════════════════════════════════════


from app.application.chat.auto_skills import (  # noqa: E402
    _FILE_TRIGGERS_WORD,
    _FILE_TRIGGERS_EXCEL,
    maybe_generate_files,
    run_auto_skills,
)

# ═══════════════════════════════════════════════════════════════
# ГЛУБОКИЙ ВЕБ-ПОИСК: поиск → заход на сайты → извлечение текста

_TEMPORAL_WEB_SEARCH = partial(_infra_do_temporal_web_search, tl=_tl)
_COLLECT_CONTEXT = partial(
    build_chat_context,
    run_tool_func=run_tool,
    append_timeline=_tl,
    temporal_web_search_func=_TEMPORAL_WEB_SEARCH,
)
_BUILD_PROMPT = partial(_app_build_prompt, run_auto_skills_func=run_auto_skills)
_APPLY_IDENTITY_GUARD = partial(_app_apply_identity_guard, append_timeline_func=_tl)
_APPLY_PROVENANCE_GUARD = partial(_app_apply_provenance_guard, append_timeline_func=_tl)


# ═══════════════════════════════════════════════════════════════
# run_agent
# ═══════════════════════════════════════════════════════════════

def run_agent(*, model_name, profile_name, user_input, session_id=None, agent_id=None, use_memory=True, use_library=True, use_reflection=False, history=None, num_ctx=8192, use_web_search=True, use_python_exec=True, use_image_gen=True, use_file_gen=True, use_http_api=True, use_sql=True, use_screenshot=True, use_encrypt=True, use_archiver=True, use_converter=True, use_regex=True, use_translator=True, use_csv=True, use_webhook=True, use_plugins=True):
    import time as _time
    _agent_start = _time.monotonic()

    # Agent OS: если указан agent_id, загружаем определение из реестра
    _registry_agent = None
    if agent_id:
        try:
            from app.services.agent_registry import resolve_agent
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
    _agent_os_source_id = _effective_agent_id
    bootstrap = bootstrap_chat_run(
        user_input=user_input,
        history=history,
        max_history_pairs=_MAX_HISTORY_PAIRS,
        trim_history_func=_app_trim_history,
        strip_frontend_project_context_func=build_strip_frontend_project_context,
        history_service=_HISTORY,
        planner_factory=PlannerV2Service,
        emit_run_started_func=_app_emit_agent_os_event,
        source_agent_id=_agent_os_source_id,
        profile_name=profile_name,
        model_name=model_name,
        session_id=str(session_id or ""),
        streaming=False,
        use_web_search=use_web_search,
        use_python_exec=use_python_exec,
        use_image_gen=use_image_gen,
        use_file_gen=use_file_gen,
        use_http_api=use_http_api,
        use_sql=use_sql,
        use_screenshot=use_screenshot,
        use_encrypt=use_encrypt,
        use_archiver=use_archiver,
        use_converter=use_converter,
        use_regex=use_regex,
        use_translator=use_translator,
        use_csv=use_csv,
        use_webhook=use_webhook,
        use_plugins=use_plugins,
    )
    history = bootstrap.history
    _disabled_skills = bootstrap.disabled_skills
    timeline = bootstrap.timeline
    tool_results = bootstrap.tool_results
    planner = bootstrap.planner
    raw_user_input = bootstrap.raw_user_input
    planner_input = bootstrap.planner_input
    run = bootstrap.run
    try:
        execution = prepare_chat_execution(
            planner_input=planner_input,
            model_name=model_name,
            plan_runner=planner.plan,
            use_memory=use_memory,
            use_library=use_library,
            use_web_search=use_web_search,
            is_memory_command_func=is_memory_command,
            pick_model_for_route_func=pick_model_for_route,
            run_id=run["run_id"],
            history_service=_HISTORY,
            extract_and_save_func=extract_and_save,
            preflight_or_raise_func=preflight_or_raise,
            agent_id=_effective_agent_id,
            num_ctx=num_ctx,
            streaming=False,
            timeline=timeline,
            append_timeline_func=_tl,
            log_memory_save=True,
        )
        plan = execution.plan
        route = execution.route
        temporal = execution.temporal
        web_plan = execution.web_plan
        effective_model = execution.effective_model
        selected = execution.selected_tools

        prompt_bundle = prepare_chat_prompt(
            profile_name=profile_name,
            raw_user_input=raw_user_input,
            planner_input=planner_input,
            route=route,
            selected_tools=selected,
            tool_results=tool_results,
            timeline=timeline,
            use_reflection=use_reflection,
            temporal=temporal,
            web_plan=web_plan,
            disabled_skills=_disabled_skills,
            has_rag=_HAS_RAG,
            is_memory_command_func=is_memory_command,
            get_relevant_context_func=get_relevant_context,
            get_rag_context_func=get_rag_context,
            collect_context_func=_COLLECT_CONTEXT,
            enrich_context_with_memory_func=_app_enrich_context_with_memory,
            build_prompt_func=_BUILD_PROMPT,
            build_task_context_func=build_task_context,
            append_timeline_func=_tl,
        )

        ctx = prompt_bundle.context_bundle
        prompt = prompt_bundle.prompt + _app_compose_human_style_rules(temporal)
        task_context = prompt_bundle.task_context
        draft = run_chat(model_name=effective_model, profile_name=profile_name, user_input=prompt, history=history, num_ctx=num_ctx, task_context=task_context)
        if not draft.get("ok"):
            raise RuntimeError("; ".join(draft.get("warnings", [])) or "LLM failed")
        answer = draft.get("answer", "")

        # Reflection: для code/project ИЛИ если пользователь включил скилл
        has_generated_files = _app_has_generated_file_attachments()
        should_reflect = (route in _REFLECTION_ROUTES) or use_reflection
        if should_reflect and answer.strip() and not has_generated_files:
            ref = run_reflection_loop(model_name=effective_model, profile_name=profile_name, user_input=raw_user_input, draft_text=answer, review_text="Улучши.", context=ctx)
            answer = ref.get("answer") or answer

        # Добавляем вложения (картинки, файлы)
        attachments = _app_get_and_clear_attachments()
        if attachments:
            answer += attachments

        guarded = apply_response_guards(
            raw_user_input=raw_user_input, text=answer, timeline=timeline,
            use_python_exec=use_python_exec, use_file_gen=use_file_gen,
            append_timeline_func=_tl, maybe_generate_files_func=maybe_generate_files,
        )
        answer = guarded.text

        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
        meta = _app_finalize_chat_success(
            history_service=_HISTORY,
            run_id=run["run_id"],
            session_id=str(session_id or ""),
            profile_name=profile_name,
            model_name=effective_model,
            route=route,
            user_input=raw_user_input,
            answer_text=answer,
            tools=selected,
            temporal=temporal,
            web_plan=web_plan,
            identity_guard=guarded.identity_guard,
            provenance_guard=guarded.provenance_guard,
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            agent_id=_effective_agent_id,
            source_agent_id=_agent_os_source_id,
            selected_tools=selected,
        )
        result = {
            "ok": True,
            "answer": answer,
            "timeline": timeline,
            "tool_results": tool_results,
            "meta": meta,
        }

        _app_record_registry_agent_run(
            agent_id=agent_id,
            registry_agent=_registry_agent,
            run_id=run["run_id"],
            input_summary=raw_user_input,
            output_summary=answer,
            ok=True,
            route=route,
            model_name=effective_model,
            duration_ms=_duration_ms,
        )
        return result
    except Exception as exc:
        if isinstance(exc, SandboxPolicyError):
            err_step = {"step": "sandbox", "title": "Sandbox", "status": "error", "detail": str(exc)}
            err_meta = {"error": str(exc), "run_id": run["run_id"], "sandbox_reason": exc.reason, "sandbox_details": exc.details}
        else:
            err_step = {"step": "error", "title": "Ошибка", "status": "error", "detail": str(exc)}
            err_meta = {"error": str(exc), "run_id": run["run_id"]}
        err = {"ok": False, "answer": "", "timeline": timeline + [err_step], "tool_results": tool_results, "meta": err_meta}
        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
        _app_finalize_chat_failure(
            history_service=_HISTORY,
            run_id=run["run_id"],
            profile_name=profile_name,
            model_name=locals().get("effective_model", model_name),
            route=locals().get("route", ""),
            error_text=str(exc),
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            agent_id=_effective_agent_id,
            source_agent_id=_agent_os_source_id,
            session_id=str(session_id or ""),
            selected_tools=locals().get("selected", []),
            history_payload=err,
        )
        if not isinstance(exc, SandboxPolicyError):
            _app_record_registry_agent_run(
                agent_id=agent_id,
                registry_agent=_registry_agent,
                run_id=run["run_id"],
                input_summary=raw_user_input if 'raw_user_input' in dir() else user_input,
                output_summary=str(exc),
                ok=False,
                route="",
                model_name=model_name,
                duration_ms=_duration_ms,
            )
        return err


# ═══════════════════════════════════════════════════════════════
# run_agent_stream
# ═══════════════════════════════════════════════════════════════

def run_agent_stream(*, model_name, profile_name, user_input, session_id=None, use_memory=True, use_library=True, use_reflection=False, history=None, num_ctx=8192, use_web_search=True, use_python_exec=True, use_image_gen=True, use_file_gen=True, use_http_api=True, use_sql=True, use_screenshot=True, use_encrypt=True, use_archiver=True, use_converter=True, use_regex=True, use_translator=True, use_csv=True, use_webhook=True, use_plugins=True):
    import time as _time
    _agent_start = _time.monotonic()
    _effective_agent_id = resolve_effective_agent_id(profile_name=profile_name)
    bootstrap = bootstrap_chat_run(
        user_input=user_input,
        history=history,
        max_history_pairs=_MAX_HISTORY_PAIRS,
        trim_history_func=_app_trim_history,
        strip_frontend_project_context_func=build_strip_frontend_project_context,
        history_service=_HISTORY,
        planner_factory=PlannerV2Service,
        emit_run_started_func=_app_emit_agent_os_event,
        source_agent_id=_effective_agent_id,
        profile_name=profile_name,
        model_name=model_name,
        session_id=str(session_id or ""),
        streaming=True,
        use_web_search=use_web_search,
        use_python_exec=use_python_exec,
        use_image_gen=use_image_gen,
        use_file_gen=use_file_gen,
        use_http_api=use_http_api,
        use_sql=use_sql,
        use_screenshot=use_screenshot,
        use_encrypt=use_encrypt,
        use_archiver=use_archiver,
        use_converter=use_converter,
        use_regex=use_regex,
        use_translator=use_translator,
        use_csv=use_csv,
        use_webhook=use_webhook,
        use_plugins=use_plugins,
    )
    history = bootstrap.history
    _disabled_skills = bootstrap.disabled_skills
    timeline = bootstrap.timeline
    tool_results = bootstrap.tool_results
    planner = bootstrap.planner
    raw_user_input = bootstrap.raw_user_input
    planner_input = bootstrap.planner_input
    run = bootstrap.run
    try:
        yield build_stream_phase_event(phase="planning", message="Думаю...")

        execution = prepare_chat_execution(
            planner_input=planner_input,
            model_name=model_name,
            plan_runner=planner.plan,
            use_memory=use_memory,
            use_library=use_library,
            use_web_search=use_web_search,
            is_memory_command_func=is_memory_command,
            pick_model_for_route_func=pick_model_for_route,
            run_id=run["run_id"],
            history_service=_HISTORY,
            extract_and_save_func=extract_and_save,
            preflight_or_raise_func=preflight_or_raise,
            agent_id=_effective_agent_id,
            num_ctx=num_ctx,
            streaming=True,
            timeline=timeline,
            append_timeline_func=_tl,
            log_auto_model_switch=True,
        )
        plan = execution.plan
        route = execution.route
        temporal = execution.temporal
        web_plan = execution.web_plan
        selected = execution.selected_tools
        effective_model = execution.effective_model

        # ═══ КЭШИРОВАНИЕ ═══
        if should_cache(planner_input, route) and not history:
            cached = get_cached(planner_input, effective_model, profile_name)
            if cached:
                cached_hit = prepare_cached_stream_hit(
                    cached_text=cached,
                    raw_user_input=raw_user_input,
                    timeline=timeline,
                    append_timeline_func=_tl,
                    apply_identity_guard_func=_APPLY_IDENTITY_GUARD,
                    apply_provenance_guard_func=_APPLY_PROVENANCE_GUARD,
                    finalize_stream_success_func=_app_finalize_stream_success,
                    history_service=_HISTORY,
                    run_id=run["run_id"],
                    session_id=str(session_id or ""),
                    profile_name=profile_name,
                    model_name=effective_model,
                    route=route,
                    temporal=temporal,
                    web_plan=web_plan,
                    num_ctx=num_ctx,
                    agent_id=_effective_agent_id,
                    source_agent_id=_effective_agent_id,
                    selected_tools=selected,
                    started_at=_agent_start,
                    monotonic_now_func=_time.monotonic,
                )
                for token_event in iter_text_stream_events(cached_hit.full_text):
                    yield token_event
                yield cached_hit.done_event
                return

        selected_tools_phase = build_selected_tools_phase_event(selected)
        if selected_tools_phase:
            yield selected_tools_phase

        yield build_stream_phase_event(phase="thinking", message="Пишу ответ...")

        prompt_bundle = prepare_chat_prompt(
            profile_name=profile_name,
            raw_user_input=raw_user_input,
            planner_input=planner_input,
            route=route,
            selected_tools=selected,
            tool_results=tool_results,
            timeline=timeline,
            use_reflection=use_reflection,
            temporal=temporal,
            web_plan=web_plan,
            disabled_skills=_disabled_skills,
            has_rag=_HAS_RAG,
            is_memory_command_func=is_memory_command,
            get_relevant_context_func=get_relevant_context,
            get_rag_context_func=get_rag_context,
            collect_context_func=_COLLECT_CONTEXT,
            enrich_context_with_memory_func=_app_enrich_context_with_memory,
            build_prompt_func=_BUILD_PROMPT,
            build_task_context_func=build_task_context,
        )

        ctx = prompt_bundle.context_bundle
        prompt = prompt_bundle.prompt + _app_compose_human_style_rules(temporal)
        full_text = ""
        task_context = prompt_bundle.task_context
        for token in run_chat_stream(model_name=effective_model, profile_name=profile_name, user_input=prompt, history=history, num_ctx=num_ctx, task_context=task_context):
            full_text += token
            yield {"token": token, "done": False}

        # Добавляем вложения (картинки, файлы) — быстрая операция
        attachments = _app_get_and_clear_attachments()
        if attachments:
            full_text += attachments

        # Проверяем нужны ли тяжёлые пост-операции
        has_generated_files = _app_has_generated_file_attachments()
        should_reflect = (route in _REFLECTION_ROUTES) or use_reflection
        ql_check = raw_user_input.lower()
        needs_file_gen = any(t in ql_check for t in _FILE_TRIGGERS_WORD + _FILE_TRIGGERS_EXCEL)

        # Если нет тяжёлых операций — быстрый путь
        if not should_reflect and not needs_file_gen:
            pass  # skip reflection
        else:
            # Тяжёлый путь — reflection и/или генерация файлов
            if should_reflect and full_text.strip() and not has_generated_files:
                yield build_stream_phase_event(phase="reflecting", message="Проверяю...")
                try:
                    ref = run_reflection_loop(model_name=effective_model, profile_name=profile_name, user_input=raw_user_input, draft_text=full_text, review_text="Улучши.", context=ctx)
                    refined = ref.get("answer", "")
                    if refined and refined != full_text:
                        full_text = refined
                        yield build_stream_phase_event(phase="reflection_replace", full_text=refined)
                except Exception:
                    pass
            if needs_file_gen:
                yield build_stream_phase_event(phase="generating_file", message="Готовлю файл...")

        # Shared post-processing: auto-exec, file gen, identity/provenance guards
        guarded = apply_response_guards(
            raw_user_input=raw_user_input, text=full_text, timeline=timeline,
            use_python_exec=use_python_exec, use_file_gen=use_file_gen,
            append_timeline_func=_tl, maybe_generate_files_func=maybe_generate_files,
        )
        full_text = guarded.text
        if guarded.changed:
            yield build_stream_phase_event(phase="reflection_replace", full_text=full_text)

        yield finalize_stream_response(
            planner_input=planner_input,
            route=route,
            profile_name=profile_name,
            model_name=effective_model,
            raw_user_input=raw_user_input,
            full_text=full_text,
            selected_tools=selected,
            temporal=temporal,
            web_plan=web_plan,
            identity_guard=guarded.identity_guard,
            provenance_guard=guarded.provenance_guard,
            history_service=_HISTORY,
            run_id=run["run_id"],
            session_id=str(session_id or ""),
            num_ctx=num_ctx,
            agent_id=_effective_agent_id,
            source_agent_id=_effective_agent_id,
            timeline=timeline,
            started_at=_agent_start,
            monotonic_now_func=_time.monotonic,
            should_cache_func=should_cache,
            set_cached_func=set_cached,
            finalize_stream_success_func=_app_finalize_stream_success,
        )
    except Exception as exc:
        _app_finalize_chat_failure(
            history_service=_HISTORY,
            run_id=run["run_id"],
            profile_name=profile_name,
            model_name=locals().get("effective_model", model_name),
            route=locals().get("route", ""),
            error_text=str(exc),
            duration_ms=int((_time.monotonic() - _agent_start) * 1000),
            streaming=True,
            num_ctx=num_ctx,
            agent_id=_effective_agent_id,
            source_agent_id=_effective_agent_id,
            session_id=str(session_id or ""),
            selected_tools=locals().get("selected", []),
            history_payload={"ok": False, "error": str(exc)},
        )
        yield {"token": "", "done": True, "error": str(exc), "full_text": ""}
