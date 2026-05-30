"""Graph-runtime assembly helpers for the V8 orchestrator."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from app.domain.agents.orchestrator_execution_runtime import (
    handle_multi_agent,
    handle_planner,
    handle_task_graph,
)
from app.domain.agents.orchestrator_postprocess_runtime import (
    handle_finalize,
    handle_reflection_v2,
)


GraphHandler = Callable[[dict[str, Any]], dict[str, Any]]
ProgressCallback = Callable[[int, int, str], None] | None
RunSelfImprovingAgentFunc = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class V8GraphRuntime:
    handlers: dict[str, GraphHandler]


def build_v8_graph_runtime(
    *,
    state: Dict[str, Any],
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int,
    run_id: str,
    total_steps: int,
    progress_callback: ProgressCallback = None,
    run_self_improving_agent_func: RunSelfImprovingAgentFunc,
) -> V8GraphRuntime:
    def progress(step: int, label: str) -> None:
        if progress_callback:
            progress_callback(step, total_steps, label)

    def h_planner(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_planner(
            current_state,
            task=task,
            model_name=model_name,
            memory_profile=memory_profile,
            num_ctx=num_ctx,
            progress_callback=lambda label: progress(1, label),
        )

    def h_task_graph(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_task_graph(
            current_state,
            task=task,
            model_name=model_name,
            memory_profile=memory_profile,
            num_ctx=num_ctx,
            progress_callback=lambda label: progress(1, label),
        )

    def h_multi_agent(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_multi_agent(
            current_state,
            task=task,
            model_name=model_name,
            memory_profile=memory_profile,
            num_ctx=num_ctx,
            progress_callback=lambda label: progress(1, label),
        )

    def h_self_improve(current_state: dict[str, Any]) -> dict[str, Any]:
        progress(1, "Self-Improving")
        result = run_self_improving_agent_func(
            task,
            model_name,
            memory_profile,
            num_ctx=num_ctx,
            max_iters=2,
            progress_callback=None,
            base_force_strategy="direct",
        )
        current_state["self_improve_result"] = result
        current_state["answer"] = (result or {}).get("answer", "") or current_state.get("answer", "")
        return current_state

    def h_reflection_v2(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_reflection_v2(
            current_state,
            task=task,
            model_name=model_name,
            memory_profile=memory_profile,
            num_ctx=num_ctx,
            progress_callback=lambda label: progress(2, label),
        )

    def h_finalize(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_finalize(
            current_state,
            task=task,
            model_name=model_name,
            num_ctx=num_ctx,
            progress_callback=lambda label: progress(total_steps, label),
        )

    handlers = {
        "planner": h_planner,
        "task_graph": h_task_graph,
        "multi_agent": h_multi_agent,
        "self_improve": h_self_improve,
        "reflection_v2": h_reflection_v2,
        "finalize": h_finalize,
    }
    return V8GraphRuntime(handlers=handlers)
