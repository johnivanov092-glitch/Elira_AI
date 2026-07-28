from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


PlanRunner = Callable[[str], dict[str, Any]]
MemoryCommandChecker = Callable[[str], bool]
HistoryTrimmer = Callable[[list[Any], int], list[Any]]
InputStripper = Callable[[str], str]
PlannerFactory = Callable[[], Any]
RunStarter = Callable[[str], dict[str, Any]]
EventEmitter = Callable[..., None]
TimelineAppender = Callable[[list[dict[str, Any]], str, str, str, str], None]
ContextCollector = Callable[..., str]
MemoryContextEnricher = Callable[..., tuple[str, int]]
PromptBuilder = Callable[..., str]
TaskContextBuilder = Callable[[str, list[str]], str]


@dataclass(frozen=True)
class ChatPlanPreparation:
    plan: dict[str, Any]
    route: str
    temporal: dict[str, Any]
    web_plan: dict[str, Any]
    selected_tools: list[str]
    effective_model: str
    decision: Any = None


@dataclass(frozen=True)
class ChatRunBootstrap:
    history: list[Any]
    disabled_skills: set[str]
    timeline: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    planner: Any
    raw_user_input: str
    planner_input: str
    run: dict[str, Any]


@dataclass(frozen=True)
class ChatExecutionPreparation:
    plan: dict[str, Any]
    route: str
    temporal: dict[str, Any]
    web_plan: dict[str, Any]
    selected_tools: list[str]
    effective_model: str
    saved_memory_items: int
    effective_num_ctx: int = 0
    effective_timeout_seconds: int | None = None
    decision: Any = None


@dataclass(frozen=True)
class ChatPromptPreparation:
    context_bundle: str
    prompt: str
    task_context: str


class OrchestrationBlocker(RuntimeError):
    """Deterministic blocker for malformed orchestration plans."""

    def __init__(self, *, reason: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"blocked": True, "reason": self.reason, "message": str(self), **self.details}

    def to_model_message(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def _planner_tool_aliases() -> set[str]:
    try:
        from app.application.monitoring.store import planner_tool_aliases

        return {str(item).strip() for item in planner_tool_aliases() if str(item).strip()}
    except Exception:
        return {
            "web_search",
            "memory_search",
            "library_context",
            "project_mode",
            "project_context",
            "python_executor",
            "project_patch",
        }


def _raw_plan_tools(plan: dict[str, Any]) -> list[str]:
    raw = plan.get("tools", [])
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = []

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _block_unknown_planner_tools(*, route: str, selected_tools: list[str]) -> None:
    known = _planner_tool_aliases()
    unknown = [tool for tool in selected_tools if tool not in known]
    if not unknown:
        return
    raise OrchestrationBlocker(
        reason="unknown_planner_tool",
        message="Planner selected unknown tool(s): " + ", ".join(unknown),
        details={
            "route": route,
            "unknown_tools": unknown,
            "known_tools": sorted(known),
        },
    )


def _parse_chat_plan(
    *,
    plan: dict[str, Any],
    planner_input: str,
    use_memory: bool,
    use_library: bool,
    use_web_search: bool,
    is_memory_command_func: MemoryCommandChecker,
) -> tuple[str, dict[str, Any], dict[str, Any], list[str]]:
    route = str(plan.get("route", "chat") or "chat")

    raw_temporal = plan.get("temporal")
    temporal = dict(raw_temporal) if isinstance(raw_temporal, dict) else {}

    raw_web_plan = plan.get("web_plan")
    if isinstance(raw_web_plan, dict) and raw_web_plan:
        web_plan = dict(raw_web_plan)
    else:
        web_plan = {"is_multi_intent": False, "subqueries": []}

    selected_tools = [
        tool_name
        for tool_name in _raw_plan_tools(plan)
        if not (tool_name == "memory_search" and not use_memory)
        and not (tool_name == "library_context" and not use_library)
        and not (tool_name == "web_search" and not use_web_search)
    ]
    if temporal.get("requires_web") and use_web_search and "web_search" not in selected_tools:
        selected_tools.append("web_search")

    strict_web_only = route == "research" and temporal.get("mode") == "hard" and temporal.get("freshness_sensitive")
    if strict_web_only:
        selected_tools = [tool_name for tool_name in selected_tools if tool_name != "memory_search"]

    if is_memory_command_func(planner_input):
        selected_tools = [tool_name for tool_name in selected_tools if tool_name != "memory_search"]

    _block_unknown_planner_tools(route=route, selected_tools=selected_tools)
    return route, temporal, web_plan, selected_tools


def build_disabled_skills(
    *,
    use_web_search: bool,
    use_python_exec: bool,
    use_image_gen: bool,
    use_file_gen: bool,
    use_http_api: bool,
    use_sql: bool,
    use_screenshot: bool,
    use_encrypt: bool,
    use_archiver: bool,
    use_converter: bool,
    use_regex: bool,
    use_translator: bool,
    use_csv: bool,
    use_webhook: bool,
    use_plugins: bool,
) -> set[str]:
    skill_flags = {
        "web_search": use_web_search,
        "python_exec": use_python_exec,
        "image_gen": use_image_gen,
        "file_gen": use_file_gen,
        "http_api": use_http_api,
        "sql": use_sql,
        "screenshot": use_screenshot,
        "encrypt": use_encrypt,
        "archiver": use_archiver,
        "converter": use_converter,
        "regex": use_regex,
        "translator": use_translator,
        "csv_analysis": use_csv,
        "webhook": use_webhook,
        "plugins": use_plugins,
    }
    return {skill_name for skill_name, is_enabled in skill_flags.items() if not is_enabled}


def bootstrap_chat_run(
    *,
    user_input: str,
    history: list[Any] | None,
    max_history_pairs: int,
    trim_history_func: HistoryTrimmer,
    strip_frontend_project_context_func: InputStripper,
    history_service: Any,
    planner_factory: PlannerFactory,
    emit_run_started_func: EventEmitter,
    source_agent_id: str,
    profile_name: str,
    model_name: str,
    session_id: str,
    streaming: bool,
    use_web_search: bool,
    use_python_exec: bool,
    use_image_gen: bool,
    use_file_gen: bool,
    use_http_api: bool,
    use_sql: bool,
    use_screenshot: bool,
    use_encrypt: bool,
    use_archiver: bool,
    use_converter: bool,
    use_regex: bool,
    use_translator: bool,
    use_csv: bool,
    use_webhook: bool,
    use_plugins: bool,
) -> ChatRunBootstrap:
    trimmed_history = trim_history_func(history or [], max_history_pairs)
    disabled_skills = build_disabled_skills(
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
    timeline: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    planner = planner_factory()
    raw_user_input = user_input
    planner_input = strip_frontend_project_context_func(user_input)
    run = history_service.start_run(raw_user_input)
    emit_run_started_func(
        event_type="agent.run.started",
        source_agent_id=source_agent_id,
        payload={
            "run_id": run["run_id"],
            "profile_name": profile_name,
            "requested_model": model_name,
            "session_id": session_id,
            "streaming": streaming,
        },
    )
    return ChatRunBootstrap(
        history=trimmed_history,
        disabled_skills=disabled_skills,
        timeline=timeline,
        tool_results=tool_results,
        planner=planner,
        raw_user_input=raw_user_input,
        planner_input=planner_input,
        run=run,
    )


def prepare_chat_plan(
    *,
    planner_input: str,
    model_name: str,
    plan_runner: PlanRunner,
    use_memory: bool,
    use_library: bool,
    use_web_search: bool,
    is_memory_command_func: MemoryCommandChecker,
    resolve_model_for_route_func: Callable[..., Any],
    available_models: list[str] | None = None,
    plan: dict[str, Any] | None = None,
) -> ChatPlanPreparation:
    raw_plan = plan if plan is not None else (plan_runner(planner_input) or {})
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    route, temporal, web_plan, selected_tools = _parse_chat_plan(
        plan=plan,
        planner_input=planner_input,
        use_memory=use_memory,
        use_library=use_library,
        use_web_search=use_web_search,
        is_memory_command_func=is_memory_command_func,
    )

    decision = resolve_model_for_route_func(route, model_name, available_models)
    return ChatPlanPreparation(
        plan=plan,
        route=route,
        temporal=temporal,
        web_plan=web_plan,
        selected_tools=selected_tools,
        effective_model=decision.model,
        decision=decision,
    )


def prepare_chat_execution(
    *,
    planner_input: str,
    model_name: str,
    plan_runner: PlanRunner,
    use_memory: bool,
    use_library: bool,
    use_web_search: bool,
    is_memory_command_func: MemoryCommandChecker,
    resolve_model_for_route_func: Callable[..., Any],
    effective_context_limit_func: Callable[..., int],
    available_models_func: Callable[[], list[str] | None],
    get_max_context_tokens_func: Callable[[str], int | None],
    record_metric_func: Callable[..., Any],
    history_service: Any,
    run_id: str,
    extract_and_save_func: Callable[[str], Any],
    preflight_or_raise_func: Callable[..., None],
    agent_id: str,
    num_ctx: int,
    streaming: bool,
    timeline: list[dict[str, Any]] | None = None,
    append_timeline_func: TimelineAppender | None = None,
    log_memory_save: bool = False,
    log_auto_model_switch: bool = False,
) -> ChatExecutionPreparation:
    raw_plan = plan_runner(planner_input) or {}
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    _parse_chat_plan(
        plan=plan,
        planner_input=planner_input,
        use_memory=use_memory,
        use_library=use_library,
        use_web_search=use_web_search,
        is_memory_command_func=is_memory_command_func,
    )
    available_models = available_models_func()
    chat_plan = prepare_chat_plan(
        planner_input=planner_input,
        model_name=model_name,
        plan_runner=lambda _text: plan,
        use_memory=use_memory,
        use_library=use_library,
        use_web_search=use_web_search,
        is_memory_command_func=is_memory_command_func,
        resolve_model_for_route_func=resolve_model_for_route_func,
        available_models=available_models,
        plan=plan,
    )
    history_service.add_event(run_id, "planner", chat_plan.plan)

    # P9.3: effective num_ctx caps the request to the tightest known limit so
    # the model AND the sandbox preflight see the same value; an explicit user
    # model cannot bypass these caps (only a selected profile contributes its
    # context_limit; an unknown model is not auto-cut to DEFAULT_SAFE_CTX).
    decision = chat_plan.decision
    profile_context_limit = (
        decision.context_limit if (decision is not None and decision.source == "profile") else None
    )
    effective_num_ctx = effective_context_limit_func(
        num_ctx,
        monitoring_max_context=get_max_context_tokens_func(agent_id),
        profile_context_limit=profile_context_limit,
        model=chat_plan.effective_model,
    )
    effective_timeout_seconds = decision.timeout_seconds if decision is not None else None

    saved_memory_items = 0
    try:
        saved = extract_and_save_func(planner_input)
        if saved:
            try:
                saved_memory_items = len(saved)
            except TypeError:
                saved_memory_items = 0
            if log_memory_save and append_timeline_func and timeline is not None:
                append_timeline_func(
                    timeline,
                    "memory_save",
                    "Память",
                    "done",
                    "Сохранено: " + str(saved_memory_items),
                )
    except Exception:
        # Business path (chat-memory persistence): surface the failure in the log
        # instead of swallowing it silently, so a broken extract_and_save is
        # visible in backend.log. Non-fatal — the turn still proceeds.
        logger.warning("chat memory save/timeline failed", exc_info=True)

    preflight_or_raise_func(
        agent_id=agent_id,
        num_ctx=effective_num_ctx,
        selected_tools=chat_plan.selected_tools,
        run_id=run_id,
        route=chat_plan.route,
        streaming=streaming,
        enforce_context_limit=False,
    )

    # P9.3: record routing provenance into run metrics (no schema change —
    # details_json). Best-effort: telemetry must never break a chat run.
    if decision is not None:
        try:
            record_metric_func(
                metric_type="model.routed",
                agent_id=agent_id,
                run_id=run_id,
                ok=True,
                details={
                    "model": decision.model,
                    "provider": decision.provider,
                    "profile_id": decision.profile_id,
                    "route": decision.route,
                    "role": decision.role,
                    "routing_source": decision.source,
                    "requested_model": decision.requested_model,
                    "effective_num_ctx": effective_num_ctx,
                    "timeout_seconds": decision.timeout_seconds,
                    "fallback_reason": decision.fallback_reason,
                    "cloud_skipped": decision.cloud_skipped,
                },
            )
        except Exception:
            pass

    if (
        log_auto_model_switch
        and append_timeline_func
        and timeline is not None
        and chat_plan.effective_model != model_name
    ):
        append_timeline_func(
            timeline,
            "auto_model",
            "Авто-модель",
            "ok",
            f"{model_name} → {chat_plan.effective_model} (route={chat_plan.route})",
        )

    return ChatExecutionPreparation(
        plan=chat_plan.plan,
        route=chat_plan.route,
        temporal=chat_plan.temporal,
        web_plan=chat_plan.web_plan,
        selected_tools=chat_plan.selected_tools,
        effective_model=chat_plan.effective_model,
        saved_memory_items=saved_memory_items,
        effective_num_ctx=effective_num_ctx,
        effective_timeout_seconds=effective_timeout_seconds,
        decision=decision,
    )


def prepare_chat_prompt(
    *,
    profile_name: str,
    raw_user_input: str,
    planner_input: str,
    route: str,
    selected_tools: list[str],
    tool_results: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    use_reflection: bool,
    temporal: dict[str, Any] | None,
    web_plan: dict[str, Any] | None,
    disabled_skills: set[str],
    has_rag: bool,
    is_memory_command_func: MemoryCommandChecker,
    get_relevant_context_func: Callable[..., str],
    get_rag_context_func: Callable[..., str],
    collect_context_func: ContextCollector,
    enrich_context_with_memory_func: MemoryContextEnricher,
    build_prompt_func: PromptBuilder,
    build_task_context_func: TaskContextBuilder,
    append_timeline_func: TimelineAppender | None = None,
) -> ChatPromptPreparation:
    context_bundle = collect_context_func(
        profile_name=profile_name,
        user_input=planner_input,
        tools=selected_tools,
        tool_results=tool_results,
        timeline=timeline,
        use_reflection=use_reflection,
        temporal=temporal,
        web_plan=web_plan,
    )

    enrich_kwargs: dict[str, Any] = {
        "planner_input": planner_input,
        "route": route,
        "temporal": temporal,
        "context": context_bundle,
        "has_rag": has_rag,
        "is_memory_command_func": is_memory_command_func,
        "get_relevant_context_func": get_relevant_context_func,
        "get_rag_context_func": get_rag_context_func,
    }
    if append_timeline_func is not None:
        enrich_kwargs["append_timeline_func"] = append_timeline_func
        enrich_kwargs["timeline"] = timeline

    context_bundle, _ = enrich_context_with_memory_func(**enrich_kwargs)
    prompt = build_prompt_func(raw_user_input, context_bundle, disabled_skills=disabled_skills)
    task_context = build_task_context_func(route, selected_tools)
    return ChatPromptPreparation(
        context_bundle=context_bundle,
        prompt=prompt,
        task_context=task_context,
    )


def build_task_context(route: str, selected_tools: list[str]) -> str:
    tools_text = ", ".join(selected_tools) if selected_tools else "нет дополнительных инструментов"
    return f"Маршрут: {route}. Инструменты: {tools_text}."
