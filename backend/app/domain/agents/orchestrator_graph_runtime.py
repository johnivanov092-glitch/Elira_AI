"""Graph-runtime assembly helpers for the V8 orchestrator."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict

from app.domain.agents.orchestrator_context_runtime import (
    handle_retrieve_kb,
    handle_retrieve_memory,
    handle_retrieve_working_memory,
    handle_tool_hint,
)
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
    from app.domain.memory.knowledge_base import record_tool_usage
    from app.domain.memory.working_memory import (
        add_working_memory,
        build_working_memory_context,
    )

    def progress(step: int, label: str) -> None:
        if progress_callback:
            progress_callback(step, total_steps, label)

    def record_working_memory(step_name: str, fact_type: str, content: str, score: float = 1.0) -> None:
        try:
            add_working_memory(
                run_id=run_id,
                step_name=step_name,
                fact_type=fact_type,
                content=(content or "")[:6000],
                score=score,
                profile_name=memory_profile,
            )
        except Exception:
            pass

    def refresh_working_context() -> None:
        try:
            state["working_context"] = build_working_memory_context(
                run_id,
                profile_name=memory_profile,
                limit=12,
            )
        except Exception:
            state["working_context"] = state.get("working_context", "")

    def record_tool(tool_name: str, ok: bool, meta: str = "") -> None:
        try:
            record_tool_usage(
                tool_name=tool_name,
                task_hint=task,
                ok=ok,
                score=1.0 if ok else 0.0,
                notes=meta[:1000],
                profile_name=memory_profile,
            )
        except Exception:
            pass

    def h_retrieve_memory(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_retrieve_memory(
            current_state,
            task=task,
            memory_profile=memory_profile,
            progress_callback=lambda label: progress(1, label),
            record_working_memory=record_working_memory,
            refresh_working_context=refresh_working_context,
        )

    def h_retrieve_kb(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_retrieve_kb(
            current_state,
            task=task,
            memory_profile=memory_profile,
            progress_callback=lambda label: progress(2, label),
            record_working_memory=record_working_memory,
            refresh_working_context=refresh_working_context,
        )

    def h_retrieve_working_memory(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_retrieve_working_memory(
            current_state,
            progress_callback=lambda label: progress(3, label),
            refresh_working_context=refresh_working_context,
        )

    def h_tool_hint(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_tool_hint(
            current_state,
            task=task,
            memory_profile=memory_profile,
            progress_callback=lambda label: progress(4, label),
            record_working_memory=record_working_memory,
            refresh_working_context=refresh_working_context,
        )

    def h_planner(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_planner(
            current_state,
            task=task,
            model_name=model_name,
            memory_profile=memory_profile,
            num_ctx=num_ctx,
            progress_callback=lambda label: progress(5, label),
            record_working_memory=record_working_memory,
            record_tool_usage=record_tool,
            refresh_working_context=refresh_working_context,
        )

    def h_task_graph(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_task_graph(
            current_state,
            task=task,
            model_name=model_name,
            memory_profile=memory_profile,
            num_ctx=num_ctx,
            progress_callback=lambda label: progress(5, label),
            record_working_memory=record_working_memory,
            record_tool_usage=record_tool,
            refresh_working_context=refresh_working_context,
        )

    def h_multi_agent(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_multi_agent(
            current_state,
            task=task,
            model_name=model_name,
            memory_profile=memory_profile,
            num_ctx=num_ctx,
            progress_callback=lambda label: progress(5, label),
            record_working_memory=record_working_memory,
            record_tool_usage=record_tool,
            refresh_working_context=refresh_working_context,
        )

    def h_self_improve(current_state: dict[str, Any]) -> dict[str, Any]:
        progress(5, "Self-Improving")
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
        if result:
            record_working_memory("self_improve", "finding", str(result)[:3000], score=0.9)
        record_tool("self_improve", True, "run_self_improving_agent")
        refresh_working_context()
        return current_state

    def h_reflection_v2(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_reflection_v2(
            current_state,
            task=task,
            model_name=model_name,
            memory_profile=memory_profile,
            num_ctx=num_ctx,
            progress_callback=lambda label: progress(6, label),
            record_working_memory=record_working_memory,
            refresh_working_context=refresh_working_context,
        )

    def h_finalize(current_state: dict[str, Any]) -> dict[str, Any]:
        return handle_finalize(
            current_state,
            task=task,
            model_name=model_name,
            num_ctx=num_ctx,
            progress_callback=lambda label: progress(total_steps, label),
            record_working_memory=record_working_memory,
            refresh_working_context=refresh_working_context,
        )

    record_working_memory(
        "route",
        "goal",
        task,
        score=1.0,
    )
    record_working_memory(
        "route",
        "decision",
        (
            f"mode={state['route'].get('mode')} "
            f"source={state['route'].get('source', 'keyword')} "
            f"confidence={state['route'].get('confidence', 0)} "
            f"reason={state['route'].get('reason', '')}"
        ),
        score=float(state["route"].get("confidence", 0.5) or 0.5),
    )
    record_working_memory(
        "strategy",
        "decision",
        json.dumps(state["strategy"], ensure_ascii=False)[:2000],
        score=float(state["strategy"].get("confidence", 0.6) or 0.6),
    )
    refresh_working_context()

    handlers = {
        "retrieve_memory": h_retrieve_memory,
        "retrieve_kb": h_retrieve_kb,
        "retrieve_working_memory": h_retrieve_working_memory,
        "tool_hint": h_tool_hint,
        "planner": h_planner,
        "task_graph": h_task_graph,
        "multi_agent": h_multi_agent,
        "self_improve": h_self_improve,
        "reflection_v2": h_reflection_v2,
        "finalize": h_finalize,
    }
    return V8GraphRuntime(
        handlers=handlers,
    )
