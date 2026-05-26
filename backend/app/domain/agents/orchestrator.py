"""V8 agent orchestrator and self-improving agent.

run_agent_v8: graph-based strategy dispatch — picks a strategy
(direct / planner / task_graph / multi_agent / self_improve) by
route + heuristics, runs the corresponding graph through
build_v8_graph_runtime, then reflects on the answer.

run_self_improving_agent: iterative critique-and-improve loop.

Heavy runtime helpers are imported lazily inside the functions to
keep this module's import surface light.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List
from uuid import uuid4

from app.domain.agents.orchestrator_graph_runtime import build_v8_graph_runtime
from app.domain.agents.orchestrator_runtime import (
    build_run_agent_v8_result,
    build_self_improving_result,
    build_v8_state,
    normalize_v8_route,
    observe_persona_dialogue,
    select_v8_graph,
)
from app.domain.agents.self_improve_runtime import run_self_improve_iterations
from app.domain.agents.router import choose_v8_strategy, route_task

# ---------------------------------------------------------------------------
# Type alias for progress callbacks
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, str], None] | None


# ---------------------------------------------------------------------------
# run_agent_v8 — graph-based strategy dispatch
# ---------------------------------------------------------------------------

def run_agent_v8(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback: ProgressCallback = None,
    force_strategy: str | None = None,
) -> dict:
    from app.domain.agents.reflection import run_graph_with_retry_v8
    run_started = time.time()
    run_id = uuid4().hex[:12]
    route = route_task(
        task, model_name=model_name,
        memory_profile=memory_profile, num_ctx=num_ctx,
    )
    route = normalize_v8_route(route)

    mode = route.get("mode", "chat") or "chat"
    strategy = choose_v8_strategy(
        task=task, route=route, model_name=model_name,
        memory_profile=memory_profile, num_ctx=num_ctx,
        force_strategy=force_strategy,
    )
    selected_strategy = strategy.get("strategy", "direct") or "direct"

    graph = select_v8_graph(selected_strategy, mode)
    total_steps = max(len(graph), 1)

    state: Dict[str, Any] = build_v8_state(
        run_id=run_id,
        task=task,
        model_name=model_name,
        memory_profile=memory_profile,
        route=route,
        mode=mode,
        strategy=strategy,
        selected_strategy=selected_strategy,
        graph=graph,
    )
    runtime = build_v8_graph_runtime(
        state=state,
        task=task,
        model_name=model_name,
        memory_profile=memory_profile,
        num_ctx=num_ctx,
        run_id=run_id,
        total_steps=total_steps,
        progress_callback=progress_callback,
        run_self_improving_agent_func=run_self_improving_agent,
    )
    state = run_graph_with_retry_v8(graph, runtime.handlers, state, max_retries=2)

    latency = round(time.time() - run_started, 3)
    reflection = state.get("reflection", {}) or {}
    answer_ok = bool(state.get("answer", "").strip()) and not state.get("failed_node")

    persona_meta = observe_persona_dialogue(
        dialog_id=run_id,
        session_id=run_id,
        profile_name=memory_profile,
        model_name=model_name,
        user_input=task,
        answer_text=state.get("answer", ""),
        route=mode,
        reflection=reflection,
        outcome_ok=answer_ok,
    )

    return build_run_agent_v8_result(
        run_id=run_id,
        mode=mode,
        route=route,
        strategy=strategy,
        selected_strategy=selected_strategy,
        graph=graph,
        state=state,
        latency=latency,
        persona_meta=persona_meta,
    )


# ---------------------------------------------------------------------------
# run_self_improving_agent — iterative critique + improve loop
# ---------------------------------------------------------------------------

def run_self_improving_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    max_iters: int = 2,
    progress_callback: ProgressCallback = None,
    base_force_strategy: str | None = None,
) -> Dict[str, Any]:
    total_steps = max(2, int(max_iters) + 1)

    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

    _progress(1, "\U0001f680 \u0411\u0430\u0437\u043e\u0432\u044b\u0439 \u0437\u0430\u043f\u0443\u0441\u043a V8")
    base = run_agent_v8(
        task=task,
        model_name=model_name,
        memory_profile=memory_profile,
        num_ctx=num_ctx,
        progress_callback=None,
        force_strategy=base_force_strategy,
    )

    run_id = base.get("run_id", "")
    loop_result = run_self_improve_iterations(
        task=task,
        model_name=model_name,
        memory_profile=memory_profile,
        num_ctx=num_ctx,
        max_iters=max_iters,
        run_id=run_id,
        answer=(base.get("answer", "") or "").strip(),
        reflection=base.get("reflection", {}) or {},
        progress_callback=lambda idx, label: _progress(
            min(idx + 1, total_steps),
            label,
        ),
    )
    answer = loop_result.get("answer", "")
    reflection: dict | Any = loop_result.get("reflection", {}) or {}
    iterations = loop_result.get("iterations", [])

    persona_meta = observe_persona_dialogue(
        dialog_id=run_id or f"self-improve-{memory_profile}",
        session_id=run_id or f"self-improve-{memory_profile}",
        profile_name=memory_profile,
        model_name=model_name,
        user_input=task,
        answer_text=answer,
        route="self_improve",
        reflection=reflection if isinstance(reflection, dict) else {},
        outcome_ok=bool(answer.strip()),
    )

    return build_self_improving_result(
        run_id=run_id,
        base=base,
        answer=answer,
        iterations=iterations,
        reflection=reflection,
        persona_meta=persona_meta,
    )
