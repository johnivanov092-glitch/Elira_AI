"""Execution/result handlers for the V8 orchestrator graph."""
from __future__ import annotations

from typing import Any, Callable, Dict


WorkingMemoryRecorder = Callable[[str, str, str, float], None]
WorkingContextRefresher = Callable[[], None]
ToolUsageRecorder = Callable[[str, bool, str], None]
StepLabelCallback = Callable[[str], None]


def extract_task_graph_answer(result: Any) -> str:
    if not isinstance(result, dict):
        return ""

    final_answer = (
        result.get("final")
        or result.get("answer")
        or result.get("summary")
        or ""
    )
    if final_answer:
        return final_answer

    logs = result.get("execution_log", []) or result.get("steps", [])
    if not logs:
        return ""
    return "\n\n".join(str(item.get("output", ""))[:2000] for item in logs[-2:])


def handle_planner(
    state: Dict[str, Any],
    *,
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int,
    progress_callback: StepLabelCallback,
    record_working_memory: WorkingMemoryRecorder,
    record_tool_usage: ToolUsageRecorder,
    refresh_working_context: WorkingContextRefresher,
) -> Dict[str, Any]:
    from app.domain.agents.planner import run_planner_agent

    progress_callback("\U0001f9ed Planner")
    plan = run_planner_agent(
        task,
        model_name,
        memory_profile,
        num_ctx=num_ctx,
        progress_callback=None,
    )
    state["plan_result"] = plan
    state["answer"] = plan.get("final") or plan.get("summary") or ""
    if plan:
        record_working_memory("planner", "decision", str(plan)[:2500], 0.85)
    record_tool_usage("planner_agent", True, "run_planner_agent")
    refresh_working_context()
    return state


def handle_task_graph(
    state: Dict[str, Any],
    *,
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int,
    progress_callback: StepLabelCallback,
    record_working_memory: WorkingMemoryRecorder,
    record_tool_usage: ToolUsageRecorder,
    refresh_working_context: WorkingContextRefresher,
) -> Dict[str, Any]:
    from app.domain.agents.planner import run_task_graph

    progress_callback("\U0001f578 Task Graph")
    result = run_task_graph(
        task,
        model_name,
        memory_profile,
        num_ctx=num_ctx,
        progress_callback=None,
    )
    state["task_graph_result"] = result
    final_answer = extract_task_graph_answer(result)
    state["answer"] = final_answer or state.get("answer", "")
    if result:
        record_working_memory("task_graph", "finding", str(result)[:3000], 0.9)
    record_tool_usage("task_graph", True, "run_task_graph")
    refresh_working_context()
    return state


def handle_multi_agent(
    state: Dict[str, Any],
    *,
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int,
    progress_callback: StepLabelCallback,
    record_working_memory: WorkingMemoryRecorder,
    record_tool_usage: ToolUsageRecorder,
    refresh_working_context: WorkingContextRefresher,
) -> Dict[str, Any]:
    from app.application.workflows.multi_agent import run_legacy_multi_agent_workflow

    progress_callback("\U0001f91d Multi-Agent")
    result = run_legacy_multi_agent_workflow(
        task=task,
        model_name=model_name,
        memory_profile=memory_profile,
        num_ctx=num_ctx,
        progress_callback=None,
    )
    state["multi_agent_result"] = result
    state["answer"] = (result or {}).get("final", "") or state.get("answer", "")
    if result:
        record_working_memory("multi_agent", "finding", str(result)[:3000], 0.92)
    record_tool_usage("multi_agent", True, "run_multi_agent")
    refresh_working_context()
    return state
