from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


PlanRunner = Callable[[str], dict[str, Any]]
MemoryCommandChecker = Callable[[str], bool]
ModelPicker = Callable[[str, str], str]
HistoryTrimmer = Callable[[list[Any], int], list[Any]]
InputStripper = Callable[[str], str]
PlannerFactory = Callable[[], Any]
RunStarter = Callable[[str], dict[str, Any]]
EventEmitter = Callable[..., None]


@dataclass(frozen=True)
class ChatPlanPreparation:
    plan: dict[str, Any]
    route: str
    temporal: dict[str, Any]
    web_plan: dict[str, Any]
    selected_tools: list[str]
    effective_model: str


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
    pick_model_for_route_func: ModelPicker,
) -> ChatPlanPreparation:
    plan = plan_runner(planner_input) or {}
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
        for tool_name in list(plan.get("tools", []) or [])
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

    effective_model = pick_model_for_route_func(route, model_name)
    return ChatPlanPreparation(
        plan=plan,
        route=route,
        temporal=temporal,
        web_plan=web_plan,
        selected_tools=selected_tools,
        effective_model=effective_model,
    )


def build_task_context(route: str, selected_tools: list[str]) -> str:
    tools_text = ", ".join(selected_tools) if selected_tools else "нет дополнительных инструментов"
    return f"Маршрут: {route}. Инструменты: {tools_text}."
