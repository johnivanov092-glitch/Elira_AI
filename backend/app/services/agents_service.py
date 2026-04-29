"""Chat agent service entry points.

This module remains the public compatibility surface for tests and runtime
callers. The active orchestration logic lives in application-layer modules.
"""
from __future__ import annotations

from functools import partial
from typing import Any

from app.application.chat.auto_skills import (
    _FILE_TRIGGERS_EXCEL,
    _FILE_TRIGGERS_WORD,
    maybe_generate_files,
    run_auto_skills,
)
from app.application.chat.context_builder import (
    collect_context as build_chat_context,
    strip_frontend_project_context as build_strip_frontend_project_context,
)
from app.application.chat.entrypoints import ChatAgentDeps, run_agent_impl, run_agent_stream_impl
from app.application.chat.finalization import (
    finalize_chat_failure as _app_finalize_chat_failure,
    finalize_chat_success as _app_finalize_chat_success,
    finalize_stream_success as _app_finalize_stream_success,
)
from app.application.chat.memory_policy import (
    enrich_context_with_memory as _app_enrich_context_with_memory,
    trim_history as _app_trim_history,
)
from app.application.chat.post_processing import (
    apply_identity_guard as _app_apply_identity_guard,
    apply_provenance_guard as _app_apply_provenance_guard,
    apply_response_guards,
    maybe_auto_exec_python as _app_maybe_auto_exec_python,
)
from app.application.chat.prompting import (
    build_prompt as _app_build_prompt,
    compose_human_style_rules as _app_compose_human_style_rules,
    get_and_clear_attachments as _app_get_and_clear_attachments,
    has_generated_file_attachments as _app_has_generated_file_attachments,
)
from app.application.chat.service import (
    bootstrap_chat_run,
    build_task_context,
    prepare_chat_execution,
    prepare_chat_prompt,
)
from app.application.chat.stream_service import (
    build_selected_tools_phase_event,
    build_stream_phase_event,
    finalize_stream_response,
    iter_text_stream_events,
    prepare_cached_stream_hit,
)
from app.application.chat.timeline import append_timeline as _app_append_timeline
from app.application.chat.agent_os import (
    emit_agent_os_event as _app_emit_agent_os_event,
    record_registry_agent_run as _app_record_registry_agent_run,
)
from app.core.config import pick_model_for_route
from app.infrastructure.search.web_search import do_temporal_web_search as _infra_do_temporal_web_search
from app.application.agent_registry.sandbox import preflight_or_raise, resolve_effective_agent_id
from app.services.chat_service import run_chat, run_chat_stream
from app.services.persona_service import observe_dialogue
from app.services.planner_v2_service import PlannerV2Service
from app.services.reflection_loop_service import run_reflection_loop
from app.services.response_cache import get_cached, set_cached, should_cache
from app.services.run_history_service import RunHistoryService
from app.services.smart_memory import extract_and_save, get_relevant_context, is_memory_command
from app.services.tool_service import run_tool

try:
    from app.services.rag_memory_service import get_rag_context

    _HAS_RAG = True
except ImportError:
    _HAS_RAG = False

    def get_rag_context(*args: Any, **kwargs: Any) -> str:
        return ""


_HISTORY = RunHistoryService()
_REFLECTION_ROUTES = {"code", "project"}
_MAX_HISTORY_PAIRS = 10

_TEMPORAL_WEB_SEARCH = partial(_infra_do_temporal_web_search, tl=_app_append_timeline)
_COLLECT_CONTEXT = partial(
    build_chat_context,
    run_tool_func=run_tool,
    append_timeline=_app_append_timeline,
    temporal_web_search_func=_TEMPORAL_WEB_SEARCH,
)
_BUILD_PROMPT = partial(_app_build_prompt, run_auto_skills_func=run_auto_skills)


def _trim_history(history: list[Any], max_history_pairs: int) -> list[Any]:
    return _app_trim_history(history, max_history_pairs)


def _strip_frontend_project_context(user_input: str) -> str:
    return build_strip_frontend_project_context(user_input)


def _collect_context(**kwargs: Any) -> str:
    return _COLLECT_CONTEXT(**kwargs)


def _build_prompt(
    user_input: str,
    context_bundle: str,
    *,
    disabled_skills: set[str] | None = None,
) -> str:
    return _BUILD_PROMPT(
        user_input=user_input,
        context_bundle=context_bundle,
        disabled_skills=disabled_skills,
    )


def _compose_human_style_rules(temporal: dict[str, Any] | None) -> str:
    return _app_compose_human_style_rules(temporal)


def _get_and_clear_attachments() -> str:
    return _app_get_and_clear_attachments()


def _has_generated_file_attachments() -> bool:
    return _app_has_generated_file_attachments()


def _emit_agent_os_event(**kwargs: Any) -> None:
    _app_emit_agent_os_event(**kwargs)


def _record_registry_agent_run(**kwargs: Any) -> None:
    _app_record_registry_agent_run(**kwargs)


def _maybe_generate_files(user_input: str, answer: str, enabled: bool = True) -> str:
    return maybe_generate_files(user_input, answer, enabled=enabled)


def _maybe_auto_exec_python(
    user_input: str,
    answer: str,
    timeline: list[dict[str, Any]],
    enabled: bool = True,
) -> str:
    return _app_maybe_auto_exec_python(
        user_input=user_input,
        answer=answer,
        enabled=enabled,
        append_timeline_func=_app_append_timeline,
        timeline=timeline,
    )


def _apply_identity_guard(
    user_input: str,
    answer_text: str,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    return _app_apply_identity_guard(
        user_input=user_input,
        answer_text=answer_text,
        append_timeline_func=_app_append_timeline,
        timeline=timeline,
    )


def _apply_provenance_guard(
    user_input: str,
    answer_text: str,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    return _app_apply_provenance_guard(
        user_input=user_input,
        answer_text=answer_text,
        append_timeline_func=_app_append_timeline,
        timeline=timeline,
    )


def _finalize_chat_success(**kwargs: Any) -> dict[str, Any]:
    return _app_finalize_chat_success(
        **kwargs,
        observe_dialogue_func=observe_dialogue,
    )


def _finalize_stream_success(**kwargs: Any) -> dict[str, Any]:
    return _app_finalize_stream_success(
        **kwargs,
        observe_dialogue_func=observe_dialogue,
    )


def _resolve_agent(**kwargs: Any) -> Any:
    from app.services.agent_registry import resolve_agent

    return resolve_agent(**kwargs)


def _build_chat_agent_deps() -> ChatAgentDeps:
    return ChatAgentDeps(
        history_service=_HISTORY,
        planner_factory=PlannerV2Service,
        resolve_effective_agent_id_func=resolve_effective_agent_id,
        resolve_agent_func=_resolve_agent,
        bootstrap_chat_run_func=bootstrap_chat_run,
        prepare_chat_execution_func=prepare_chat_execution,
        prepare_chat_prompt_func=prepare_chat_prompt,
        trim_history_func=_trim_history,
        strip_frontend_project_context_func=_strip_frontend_project_context,
        emit_agent_os_event_func=_emit_agent_os_event,
        collect_context_func=_collect_context,
        build_prompt_func=_build_prompt,
        compose_human_style_rules_func=_compose_human_style_rules,
        get_and_clear_attachments_func=_get_and_clear_attachments,
        has_generated_file_attachments_func=_has_generated_file_attachments,
        build_task_context_func=build_task_context,
        append_timeline_func=_app_append_timeline,
        enrich_context_with_memory_func=_app_enrich_context_with_memory,
        run_chat_func=run_chat,
        run_chat_stream_func=run_chat_stream,
        run_reflection_loop_func=run_reflection_loop,
        apply_response_guards_func=apply_response_guards,
        apply_identity_guard_func=_apply_identity_guard,
        apply_provenance_guard_func=_apply_provenance_guard,
        maybe_generate_files_func=_maybe_generate_files,
        maybe_auto_exec_python_func=_maybe_auto_exec_python,
        finalize_chat_success_func=_finalize_chat_success,
        finalize_chat_failure_func=_app_finalize_chat_failure,
        finalize_stream_success_func=_finalize_stream_success,
        finalize_stream_response_func=finalize_stream_response,
        build_stream_phase_event_func=build_stream_phase_event,
        build_selected_tools_phase_event_func=build_selected_tools_phase_event,
        iter_text_stream_events_func=iter_text_stream_events,
        prepare_cached_stream_hit_func=prepare_cached_stream_hit,
        record_registry_agent_run_func=_record_registry_agent_run,
        is_memory_command_func=is_memory_command,
        pick_model_for_route_func=pick_model_for_route,
        extract_and_save_func=extract_and_save,
        preflight_or_raise_func=preflight_or_raise,
        should_cache_func=should_cache,
        get_cached_func=get_cached,
        set_cached_func=set_cached,
        get_relevant_context_func=get_relevant_context,
        get_rag_context_func=get_rag_context,
        has_rag=_HAS_RAG,
        reflection_routes=_REFLECTION_ROUTES,
        max_history_pairs=_MAX_HISTORY_PAIRS,
        file_trigger_words=tuple(_FILE_TRIGGERS_WORD),
        file_trigger_excel=tuple(_FILE_TRIGGERS_EXCEL),
    )


def run_agent(
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
    return run_agent_impl(
        deps=_build_chat_agent_deps(),
        model_name=model_name,
        profile_name=profile_name,
        user_input=user_input,
        session_id=session_id,
        agent_id=agent_id,
        use_memory=use_memory,
        use_library=use_library,
        use_reflection=use_reflection,
        history=history,
        num_ctx=num_ctx,
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


def run_agent_stream(
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
):
    yield from run_agent_stream_impl(
        deps=_build_chat_agent_deps(),
        model_name=model_name,
        profile_name=profile_name,
        user_input=user_input,
        session_id=session_id,
        use_memory=use_memory,
        use_library=use_library,
        use_reflection=use_reflection,
        history=history,
        num_ctx=num_ctx,
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
