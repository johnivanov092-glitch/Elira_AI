"""Runtime helpers for the V8 orchestrator."""
from __future__ import annotations

from typing import Any, Dict, List

from app.domain.agents.router import TASK_GRAPH_TEMPLATES_V8


_FALLBACK_ROUTE = {
    "mode": "chat",
    "agent": "chat_agent",
    "use_graph": False,
    "confidence": 0.0,
    "source": "fallback",
    "reason": "route_task returned None or invalid data",
}

_GRAPH_MAP = {
    "direct": [
        "retrieve_memory", "retrieve_kb",
        "retrieve_working_memory", "finalize",
    ],
    "planner": [
        "retrieve_memory", "retrieve_kb",
        "retrieve_working_memory", "planner",
        "reflection_v2", "finalize",
    ],
    "task_graph": [
        "retrieve_memory", "retrieve_kb",
        "retrieve_working_memory", "tool_hint",
        "task_graph", "reflection_v2", "finalize",
    ],
    "multi_agent": [
        "retrieve_memory", "retrieve_kb",
        "retrieve_working_memory", "multi_agent",
        "reflection_v2", "finalize",
    ],
    "self_improve": [
        "retrieve_memory", "retrieve_kb",
        "retrieve_working_memory", "self_improve",
        "finalize",
    ],
}


def normalize_v8_route(route: Any) -> dict[str, Any]:
    if isinstance(route, dict):
        return route
    return dict(_FALLBACK_ROUTE)


def select_v8_graph(selected_strategy: str, mode: str) -> List[str]:
    return (
        _GRAPH_MAP.get(selected_strategy)
        or TASK_GRAPH_TEMPLATES_V8.get(
            mode,
            ["retrieve_memory", "retrieve_working_memory", "finalize"],
        )
    )


def build_v8_state(
    *,
    run_id: str,
    task: str,
    model_name: str,
    memory_profile: str,
    route: dict[str, Any],
    mode: str,
    strategy: dict[str, Any],
    selected_strategy: str,
    graph: List[str],
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "task": task,
        "model_name": model_name,
        "memory_profile": memory_profile,
        "route": route,
        "mode": mode,
        "strategy": strategy,
        "selected_strategy": selected_strategy,
        "graph": graph,
        "memory_context": "",
        "kb_context": "",
        "working_context": "",
        "tool_hint": "",
        "plan_result": None,
        "task_graph_result": None,
        "multi_agent_result": None,
        "self_improve_result": None,
        "answer": "",
        "reflection": {},
        "errors": [],
        "timeline": [],
    }


def compute_reflection_quality_score(reflection: Any) -> float:
    if not reflection:
        return 1.0
    return (
        0.2
        + 0.2 * float(bool(reflection.get("answered", True)))
        + 0.2 * float(bool(reflection.get("grounded", True)))
        + 0.2 * float(bool(reflection.get("complete", True)))
        + 0.2 * float(bool(reflection.get("actionable", True)))
    )


def observe_persona_dialogue(
    *,
    dialog_id: str,
    session_id: str,
    profile_name: str,
    model_name: str,
    user_input: str,
    answer_text: str,
    route: str,
    reflection: dict[str, Any],
    outcome_ok: bool,
) -> Any:
    try:
        from app.application.persona.service import observe_dialogue

        return observe_dialogue(
            dialog_id=dialog_id,
            session_id=session_id,
            profile_name=profile_name,
            model_name=model_name,
            user_input=user_input,
            answer_text=answer_text,
            route=route,
            reflection=reflection,
            outcome_ok=outcome_ok,
        )
    except Exception:
        return None


def build_run_agent_v8_result(
    *,
    run_id: str,
    mode: str,
    route: dict[str, Any],
    strategy: dict[str, Any],
    selected_strategy: str,
    graph: List[str],
    state: dict[str, Any],
    latency: float,
    persona_meta: Any,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "mode": mode,
        "route": route,
        "strategy": strategy,
        "delegated_strategy": selected_strategy,
        "graph": graph,
        "answer": state.get("answer", ""),
        "reflection": state.get("reflection", {}),
        "task_graph_result": state.get("task_graph_result"),
        "plan_result": state.get("plan_result"),
        "multi_agent_result": state.get("multi_agent_result"),
        "self_improve_result": state.get("self_improve_result"),
        "errors": state.get("errors", []),
        "timeline": state.get("timeline", []),
        "failed_node": state.get("failed_node", ""),
        "memory_context": state.get("memory_context", ""),
        "kb_context": state.get("kb_context", ""),
        "working_context": state.get("working_context", ""),
        "tool_hint": state.get("tool_hint", ""),
        "latency_seconds": latency,
        "persona": persona_meta,
    }


def build_self_improving_result(
    *,
    run_id: str,
    base: dict[str, Any],
    answer: str,
    iterations: List[Dict[str, Any]],
    reflection: Any,
    working_context: str,
    persona_meta: Any,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "base": base,
        "answer": answer,
        "iterations": iterations,
        "final_reflection": reflection,
        "mode": base.get("mode", ""),
        "route": base.get("route", {}),
        "graph": base.get("graph", []),
        "timeline": base.get("timeline", []),
        "errors": base.get("errors", []),
        "memory_context": base.get("memory_context", ""),
        "kb_context": base.get("kb_context", ""),
        "working_context": working_context or base.get("working_context", ""),
        "persona": persona_meta,
    }
