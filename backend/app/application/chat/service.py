from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


PlanRunner = Callable[[str], dict[str, Any]]
MemoryCommandChecker = Callable[[str], bool]
ModelPicker = Callable[[str, str], str]


@dataclass(frozen=True)
class ChatPlanPreparation:
    plan: dict[str, Any]
    route: str
    temporal: dict[str, Any]
    web_plan: dict[str, Any]
    selected_tools: list[str]
    effective_model: str


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
