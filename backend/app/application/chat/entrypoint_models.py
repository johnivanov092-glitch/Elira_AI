from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ChatAgentDeps:
    history_service: Any
    planner_factory: Callable[[], Any]
    resolve_effective_agent_id_func: Callable[..., str]
    resolve_agent_func: Callable[..., Any] | None
    bootstrap_chat_run_func: Callable[..., Any]
    prepare_chat_execution_func: Callable[..., Any]
    prepare_chat_prompt_func: Callable[..., Any]
    trim_history_func: Callable[..., Any]
    strip_frontend_project_context_func: Callable[[str], str]
    emit_agent_os_event_func: Callable[..., None]
    collect_context_func: Callable[..., str]
    build_prompt_func: Callable[..., str]
    compose_human_style_rules_func: Callable[[dict[str, Any] | None], str]
    get_and_clear_attachments_func: Callable[[], str]
    has_generated_file_attachments_func: Callable[[], bool]
    build_task_context_func: Callable[[str, list[str]], str]
    append_timeline_func: Callable[..., None]
    enrich_context_with_memory_func: Callable[..., Any]
    run_chat_func: Callable[..., dict[str, Any]]
    run_chat_stream_func: Callable[..., Any]
    run_reflection_loop_func: Callable[..., dict[str, Any]]
    apply_response_guards_func: Callable[..., Any]
    apply_identity_guard_func: Callable[[str, str, list[dict[str, Any]]], dict[str, Any]]
    apply_provenance_guard_func: Callable[[str, str, list[dict[str, Any]]], dict[str, Any]]
    maybe_generate_files_func: Callable[..., str]
    maybe_auto_exec_python_func: Callable[..., str]
    finalize_chat_success_func: Callable[..., dict[str, Any]]
    finalize_chat_failure_func: Callable[..., None]
    finalize_stream_success_func: Callable[..., dict[str, Any]]
    finalize_stream_response_func: Callable[..., dict[str, Any]]
    build_stream_phase_event_func: Callable[..., dict[str, Any]]
    build_selected_tools_phase_event_func: Callable[..., dict[str, Any] | None]
    iter_text_stream_events_func: Callable[[str], Any]
    prepare_cached_stream_hit_func: Callable[..., Any]
    record_registry_agent_run_func: Callable[..., None]
    is_memory_command_func: Callable[[str], bool]
    pick_model_for_route_func: Callable[[str, str], str]
    extract_and_save_func: Callable[..., Any]
    preflight_or_raise_func: Callable[..., None]
    should_cache_func: Callable[..., bool]
    get_cached_func: Callable[..., str | None]
    set_cached_func: Callable[..., None]
    get_relevant_context_func: Callable[..., str]
    get_rag_context_func: Callable[..., str]
    has_rag: bool
    reflection_routes: set[str]
    max_history_pairs: int
    file_trigger_words: tuple[str, ...]
    file_trigger_excel: tuple[str, ...]
