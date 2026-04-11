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

import logging
from functools import partial
from typing import Any, Generator

from app.application.chat.context_builder import (
    collect_context as build_chat_context,
    strip_frontend_project_context as build_strip_frontend_project_context,
)
from app.application.chat.service import (
    bootstrap_chat_run,
    build_task_context,
    prepare_chat_execution,
)
from app.application.chat.prompting import (
    build_prompt as _app_build_prompt,
    build_runtime_datetime_context as _app_build_runtime_datetime_context,
    compose_human_style_rules as _app_compose_human_style_rules,
    get_and_clear_attachments as _app_get_and_clear_attachments,
    has_generated_file_attachments as _app_has_generated_file_attachments,
    wants_explicit_datetime_answer as _app_wants_explicit_datetime_answer,
)
from app.application.chat.memory_policy import (
    enrich_context_with_memory as _app_enrich_context_with_memory,
    get_memory_recall_limits as _app_get_memory_recall_limits,
    is_direct_personal_memory_query as _app_is_direct_personal_memory_query,
    should_recall_memory_context as _app_should_recall_memory_context,
    trim_history as _app_trim_history,
)
from app.application.chat.post_processing import (
    apply_identity_guard as _app_apply_identity_guard,
    apply_provenance_guard as _app_apply_provenance_guard,
    maybe_auto_exec_python as _app_maybe_auto_exec_python,
)
from app.application.chat.agent_os import (
    emit_agent_os_event as _app_emit_agent_os_event,
    record_agent_os_monitoring as _app_record_agent_os_monitoring,
    resolve_agent_os_source_id as _app_resolve_agent_os_source_id,
)
from app.application.chat.finalization import (
    finalize_chat_failure as _app_finalize_chat_failure,
    finalize_chat_success as _app_finalize_chat_success,
    finalize_stream_success as _app_finalize_stream_success,
)
from app.application.chat.stream_service import (
    iter_text_stream_events,
    prepare_cached_stream_hit,
)
from app.infrastructure.search.web_search import (
    build_single_web_subquery_context as _infra_build_single_web_subquery_context,
    clean_query as _infra_clean_query,
    do_temporal_web_search as _infra_do_temporal_web_search,
    do_temporal_web_search_legacy as _infra_do_temporal_web_search_legacy,
    do_web_search as _infra_do_web_search,
    do_web_search_legacy as _infra_do_web_search_legacy,
    get_web_search_result as _infra_get_web_search_result,
    is_strict_web_only_query as _infra_is_strict_web_only_query,
)
from app.services.agent_monitor import record_agent_run_metric
from app.services.agent_sandbox import (
    SandboxPolicyError,
    preflight_or_raise,
    resolve_effective_agent_id,
)
from app.services.chat_service import run_chat, run_chat_stream
from app.services.planner_v2_service import PlannerV2Service
from app.services.reflection_loop_service import run_reflection_loop
from app.services.run_history_service import RunHistoryService
from app.services.temporal_intent import detect_temporal_intent
from app.services.tool_service import run_tool
from app.services.smart_memory import extract_and_save, get_relevant_context, is_memory_command
from app.services.response_cache import get_cached, set_cached, should_cache
from app.core.config import pick_model_for_route, DEFAULT_MODEL

# RAG память (опционально — если embedding модель доступна)
try:
    from app.services.rag_memory_service import get_rag_context, add_to_rag
    _HAS_RAG = True
except ImportError:
    _HAS_RAG = False
    def get_rag_context(*a, **kw): return ""
    def add_to_rag(*a, **kw): return {}

logger = logging.getLogger(__name__)

_HISTORY = RunHistoryService()
_REFLECTION_ROUTES = {"code", "project"}
_MAX_HISTORY_PAIRS = 10


def _short(v, limit=600):
    t = str(v or ""); return t if len(t) <= limit else t[:limit] + "..."

def _tl(timeline, step, title, status, detail):
    timeline.append({"step": step, "title": title, "status": status, "detail": detail})


def _apply_identity_guard(user_input: str, answer_text: str, timeline: list[dict[str, Any]]):
    return _app_apply_identity_guard(
        user_input=user_input,
        answer_text=answer_text,
        append_timeline_func=_tl,
        timeline=timeline,
    )

def _apply_provenance_guard(user_input: str, answer_text: str, timeline: list[dict[str, Any]]):
    return _app_apply_provenance_guard(
        user_input=user_input,
        answer_text=answer_text,
        append_timeline_func=_tl,
        timeline=timeline,
    )


def _resolve_agent_os_source_id(agent_id: str | None, registry_agent: dict[str, Any] | None) -> str:
    """Facade -- delegates to application.chat.agent_os."""
    return _app_resolve_agent_os_source_id(agent_id, registry_agent)


def _emit_agent_os_event(*, event_type: str, source_agent_id: str = "", payload: dict[str, Any] | None = None) -> None:
    """Facade -- delegates to application.chat.agent_os."""
    _app_emit_agent_os_event(
        event_type=event_type,
        source_agent_id=source_agent_id,
        payload=payload,
    )


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
    """Facade -- delegates to application.chat.agent_os."""
    _app_record_agent_os_monitoring(
        agent_id=agent_id,
        run_id=run_id,
        route=route,
        model_name=model_name,
        ok=ok,
        duration_ms=duration_ms,
        streaming=streaming,
        num_ctx=num_ctx,
        selected_tools=selected_tools,
    )


def _is_direct_personal_memory_query(user_input: str) -> bool:
    return _app_is_direct_personal_memory_query(user_input)


def _should_recall_memory_context(user_input: str, route: str, temporal: dict[str, Any] | None) -> bool:
    return _app_should_recall_memory_context(
        user_input,
        route,
        temporal,
        is_memory_command_func=is_memory_command,
    )


def _get_memory_recall_limits(user_input: str) -> tuple[int, int]:
    return _app_get_memory_recall_limits(user_input)


def _trim_history(h, max_pairs=_MAX_HISTORY_PAIRS):
    return _app_trim_history(h, max_pairs=max_pairs)



def _maybe_auto_exec_python(user_input, answer, timeline, enabled: bool = True):
    return _app_maybe_auto_exec_python(
        user_input=user_input,
        answer=answer,
        enabled=enabled,
        append_timeline_func=_tl,
        timeline=timeline,
    )


# ═══════════════════════════════════════════════════════════════
# POST-ГЕНЕРАЦИЯ ФАЙЛОВ: LLM написал ответ → сохраняем в Word/Excel
# ═══════════════════════════════════════════════════════════════


# Re-export file trigger constants from extracted module
from app.application.chat.auto_skills import (  # noqa: E402
    _FILE_TRIGGERS_WORD,
    _FILE_TRIGGERS_EXCEL,
)


def _maybe_generate_files(user_input: str, llm_answer: str, enabled: bool = True) -> str:
    """Facade -- delegates to application.chat.auto_skills."""
    from app.application.chat.auto_skills import maybe_generate_files
    return maybe_generate_files(user_input, llm_answer, enabled=enabled)


def _run_auto_skills(user_input: str, disabled: set | None = None) -> str:
    """Facade -- delegates to application.chat.auto_skills."""
    from app.application.chat.auto_skills import run_auto_skills
    return run_auto_skills(user_input, disabled=disabled)


def _compose_human_style_rules(temporal: dict[str, Any] | None) -> str:
    return _app_compose_human_style_rules(temporal)




def _wants_explicit_datetime_answer(user_input: str) -> bool:
    return _app_wants_explicit_datetime_answer(user_input)


def _build_runtime_datetime_context(user_input: str) -> str:
    return _app_build_runtime_datetime_context(user_input)


def _build_prompt(user_input, context_bundle, mode="default", disabled_skills: set | None = None):
    return _app_build_prompt(
        user_input=user_input,
        context_bundle=context_bundle,
        run_auto_skills_func=_run_auto_skills,
        disabled_skills=disabled_skills,
    )

def _get_and_clear_attachments() -> str:
    """Facade -- delegates to application.chat.prompting."""
    return _app_get_and_clear_attachments()


# ═══════════════════════════════════════════════════════════════
# ГЛУБОКИЙ ВЕБ-ПОИСК: поиск → заход на сайты → извлечение текста

_clean_query = _infra_clean_query
_is_strict_web_only_query = _infra_is_strict_web_only_query
_get_web_search_result = _infra_get_web_search_result
_build_single_web_subquery_context = _infra_build_single_web_subquery_context
_do_web_search_legacy = partial(_infra_do_web_search_legacy, tl=_tl)
_do_temporal_web_search_legacy = partial(_infra_do_temporal_web_search_legacy, tl=_tl)
_do_web_search = partial(_infra_do_web_search, tl=_tl)
_do_temporal_web_search = partial(_infra_do_temporal_web_search, tl=_tl)




# ═══════════════════════════════════════════════════════════════
def _strip_frontend_project_context(user_input: str) -> str:
    return build_strip_frontend_project_context(user_input)


def _collect_context(**kwargs):
    return build_chat_context(
        run_tool_func=run_tool,
        append_timeline=_tl,
        temporal_web_search_func=_do_temporal_web_search,
        **kwargs,
    )

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
        trim_history_func=_trim_history,
        strip_frontend_project_context_func=_strip_frontend_project_context,
        history_service=_HISTORY,
        planner_factory=PlannerV2Service,
        emit_run_started_func=_emit_agent_os_event,
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

        ctx = _collect_context(profile_name=profile_name, user_input=planner_input, tools=selected, tool_results=tool_results, timeline=timeline, use_reflection=use_reflection, temporal=temporal, web_plan=web_plan)

        ctx, _ = _app_enrich_context_with_memory(
            planner_input=planner_input,
            route=route,
            temporal=temporal,
            context=ctx,
            has_rag=_HAS_RAG,
            is_memory_command_func=is_memory_command,
            get_relevant_context_func=get_relevant_context,
            get_rag_context_func=get_rag_context,
            append_timeline_func=_tl,
            timeline=timeline,
        )

        prompt = _build_prompt(raw_user_input, ctx, disabled_skills=_disabled_skills) + _compose_human_style_rules(temporal)
        task_context = build_task_context(route, selected)
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
        attachments = _get_and_clear_attachments()
        if attachments:
            answer += attachments

        # POST-генерация: Word/Excel из ответа LLM
        post_files = _maybe_generate_files(raw_user_input, answer, enabled=use_file_gen)
        if post_files:
            answer += post_files

        identity_guard = _apply_identity_guard(raw_user_input, answer, timeline)
        answer = identity_guard.get("text", answer)
        provenance_guard = _apply_provenance_guard(raw_user_input, answer, timeline)
        answer = provenance_guard.get("text", answer)

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
            identity_guard=identity_guard,
            provenance_guard=provenance_guard,
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

        # Agent OS: записываем запуск в реестр
        if agent_id or _registry_agent:
            try:
                from app.services.agent_registry import record_agent_run
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
        return err
    except Exception as exc:
        err = {"ok": False, "answer": "", "timeline": timeline + [{"step": "error", "title": "Ошибка", "status": "error", "detail": str(exc)}], "tool_results": tool_results, "meta": {"error": str(exc), "run_id": run["run_id"]}}
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
        if agent_id or _registry_agent:
            try:
                from app.services.agent_registry import record_agent_run
                record_agent_run({
                    "agent_id": agent_id or (_registry_agent or {}).get("id", ""),
                    "run_id": run["run_id"],
                    "input_summary": raw_user_input[:500] if 'raw_user_input' in dir() else user_input[:500],
                    "output_summary": str(exc)[:500],
                    "ok": False,
                    "route": "",
                    "model_used": model_name,
                    "duration_ms": _duration_ms,
                })
            except Exception:
                pass

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
        trim_history_func=_trim_history,
        strip_frontend_project_context_func=_strip_frontend_project_context,
        history_service=_HISTORY,
        planner_factory=PlannerV2Service,
        emit_run_started_func=_emit_agent_os_event,
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
        yield {"token": "", "done": False, "phase": "planning", "message": "Думаю..."}

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
                    apply_identity_guard_func=_apply_identity_guard,
                    apply_provenance_guard_func=_apply_provenance_guard,
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

        if "web_search" in selected:
            yield {"token": "", "done": False, "phase": "searching", "message": "Ищу..."}
        elif selected:
            yield {"token": "", "done": False, "phase": "tools", "message": "Собираю контекст..."}

        ctx = _collect_context(profile_name=profile_name, user_input=planner_input, tools=selected, tool_results=tool_results, timeline=timeline, use_reflection=use_reflection, temporal=temporal, web_plan=web_plan)

        ctx, _ = _app_enrich_context_with_memory(
            planner_input=planner_input,
            route=route,
            temporal=temporal,
            context=ctx,
            has_rag=_HAS_RAG,
            is_memory_command_func=is_memory_command,
            get_relevant_context_func=get_relevant_context,
            get_rag_context_func=get_rag_context,
        )

        yield {"token": "", "done": False, "phase": "thinking", "message": "Пишу ответ..."}

        prompt = _build_prompt(raw_user_input, ctx, disabled_skills=_disabled_skills) + _compose_human_style_rules(temporal)
        full_text = ""
        task_context = build_task_context(route, selected)
        for token in run_chat_stream(model_name=effective_model, profile_name=profile_name, user_input=prompt, history=history, num_ctx=num_ctx, task_context=task_context):
            full_text += token
            yield {"token": token, "done": False}

        # Добавляем вложения (картинки, файлы) — быстрая операция
        attachments = _get_and_clear_attachments()
        if attachments:
            full_text += attachments

        # Проверяем нужны ли тяжёлые пост-операции
        has_generated_files = _app_has_generated_file_attachments()
        should_reflect = (route in _REFLECTION_ROUTES) or use_reflection
        ql_check = raw_user_input.lower()
        needs_file_gen = any(t in ql_check for t in _FILE_TRIGGERS_WORD + _FILE_TRIGGERS_EXCEL)

        # Если нет тяжёлых операций — отправляем done СРАЗУ (быстрый путь)
        if not should_reflect and not needs_file_gen:
            # Авто-выполнение Python (лёгкое, только если есть код)
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
            _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
            yield _app_finalize_stream_success(
                history_service=_HISTORY,
                run_id=run["run_id"],
                session_id=str(session_id or ""),
                profile_name=profile_name,
                model_name=effective_model,
                route=route,
                user_input=raw_user_input,
                full_text=full_text,
                tools=selected,
                temporal=temporal,
                web_plan=web_plan,
                identity_guard=identity_guard,
                provenance_guard=provenance_guard,
                duration_ms=_duration_ms,
                num_ctx=num_ctx,
                agent_id=_effective_agent_id,
                source_agent_id=_effective_agent_id,
                timeline=timeline,
                selected_tools=selected,
            )
        else:
            # Тяжёлый путь — reflection и/или генерация файлов
            if should_reflect and full_text.strip() and not has_generated_files:
                yield {"token": "", "done": False, "phase": "reflecting", "message": "Проверяю..."}
                try:
                    ref = run_reflection_loop(model_name=effective_model, profile_name=profile_name, user_input=raw_user_input, draft_text=full_text, review_text="Улучши.", context=ctx)
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

            # Кэшируем после всех пост-обработок
            if should_cache(planner_input, route) and full_text.strip():
                try:
                    set_cached(planner_input, effective_model, profile_name, full_text)
                except Exception:
                    pass

            _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
            yield _app_finalize_stream_success(
                history_service=_HISTORY,
                run_id=run["run_id"],
                session_id=str(session_id or ""),
                profile_name=profile_name,
                model_name=effective_model,
                route=route,
                user_input=raw_user_input,
                full_text=full_text,
                tools=selected,
                temporal=temporal,
                web_plan=web_plan,
                identity_guard=identity_guard,
                provenance_guard=provenance_guard,
                duration_ms=_duration_ms,
                num_ctx=num_ctx,
                agent_id=_effective_agent_id,
                source_agent_id=_effective_agent_id,
                timeline=timeline,
                selected_tools=selected,
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
