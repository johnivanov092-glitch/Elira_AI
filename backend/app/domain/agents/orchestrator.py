"""V8 agent orchestrator and self-improving agent.

Extracted from core/agents.py — run_agent_v8 (graph-based strategy
dispatch with memory, KB, tool hints, and reflection) and
run_self_improving_agent (iterative critique-and-improve loop).

Heavy runtime helpers are imported lazily from their canonical modules
to avoid circular imports.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List
from uuid import uuid4

from app.core.llm import ask_model
from app.application.workflows.multi_agent import run_legacy_multi_agent_workflow
from app.domain.agents.orchestrator_context_runtime import (
    handle_retrieve_kb,
    handle_retrieve_memory,
    handle_retrieve_working_memory,
    handle_tool_hint,
)
from app.domain.agents.planner import run_planner_agent, run_task_graph
from app.domain.agents.orchestrator_runtime import (
    build_run_agent_v8_result,
    build_self_improving_result,
    build_v8_state,
    compute_reflection_quality_score,
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
    from app.domain.memory.knowledge_base import (
        record_tool_usage,
    )
    from app.domain.memory.strategy_tracking import record_v8_strategy_usage
    from app.domain.memory.task_tracking import record_task_run
    from app.domain.memory.working_memory import (
        add_working_memory,
        build_working_memory_context,
    )
    from app.domain.agents.reflection import (
        reflection_v2,
        count_false_flags,
        regenerate_answer_from_context,
        run_graph_with_retry_v8,
    )
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

    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

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

    # -- working-memory helpers --
    def _wm(step_name: str, fact_type: str, content: str, score: float = 1.0):
        try:
            add_working_memory(
                run_id=run_id, step_name=step_name,
                fact_type=fact_type,
                content=(content or "")[:6000],
                score=score, profile_name=memory_profile,
            )
        except Exception:
            pass

    def _refresh_working():
        try:
            state["working_context"] = build_working_memory_context(
                run_id, profile_name=memory_profile, limit=12,
            )
        except Exception:
            state["working_context"] = state.get("working_context", "")

    def _record_tool(tool_name: str, ok: bool, meta: str = ""):
        try:
            record_tool_usage(
                tool_name=tool_name, task_hint=task,
                ok=ok, score=1.0 if ok else 0.0,
                notes=meta[:1000], profile_name=memory_profile,
            )
        except Exception:
            pass

    _wm("route", "goal", task, score=1.0)
    _wm(
        "route", "decision",
        f"mode={route.get('mode')} source={route.get('source', 'keyword')} "
        f"confidence={route.get('confidence', 0)} reason={route.get('reason', '')}",
        score=float(route.get("confidence", 0.5) or 0.5),
    )
    _wm(
        "strategy", "decision",
        json.dumps(strategy, ensure_ascii=False)[:2000],
        score=float(strategy.get("confidence", 0.6) or 0.6),
    )
    _refresh_working()

    # -- graph node handlers --
    def h_retrieve_memory(s: dict) -> dict:
        return handle_retrieve_memory(
            s,
            task=task,
            memory_profile=memory_profile,
            progress_callback=lambda label: _progress(1, label),
            record_working_memory=_wm,
            refresh_working_context=_refresh_working,
        )

    def h_retrieve_kb(s: dict) -> dict:
        return handle_retrieve_kb(
            s,
            task=task,
            memory_profile=memory_profile,
            progress_callback=lambda label: _progress(2, label),
            record_working_memory=_wm,
            refresh_working_context=_refresh_working,
        )

    def h_retrieve_working_memory(s: dict) -> dict:
        return handle_retrieve_working_memory(
            s,
            progress_callback=lambda label: _progress(3, label),
            refresh_working_context=_refresh_working,
        )

    def h_tool_hint(s: dict) -> dict:
        return handle_tool_hint(
            s,
            task=task,
            memory_profile=memory_profile,
            progress_callback=lambda label: _progress(4, label),
            record_working_memory=_wm,
            refresh_working_context=_refresh_working,
        )

    def h_planner(s: dict) -> dict:
        _progress(5, "\U0001f9ed Planner")
        plan = run_planner_agent(
            task, model_name, memory_profile,
            num_ctx=num_ctx, progress_callback=None,
        )
        s["plan_result"] = plan
        s["answer"] = plan.get("final") or plan.get("summary") or ""
        if plan:
            _wm("planner", "decision", str(plan)[:2500], score=0.85)
        _record_tool("planner_agent", True, "run_planner_agent")
        _refresh_working()
        return s

    def h_task_graph(s: dict) -> dict:
        _progress(5, "\U0001f578 Task Graph")
        result = run_task_graph(
            task, model_name, memory_profile,
            num_ctx=num_ctx, progress_callback=None,
        )
        s["task_graph_result"] = result
        final_answer = ""
        if isinstance(result, dict):
            final_answer = (
                result.get("final")
                or result.get("answer")
                or result.get("summary")
                or ""
            )
        if not final_answer and isinstance(result, dict):
            logs = result.get("execution_log", []) or result.get("steps", [])
            if logs:
                final_answer = "\n\n".join(
                    str(x.get("output", ""))[:2000] for x in logs[-2:]
                )
        s["answer"] = final_answer or s.get("answer", "")
        if result:
            _wm("task_graph", "finding", str(result)[:3000], score=0.9)
        _record_tool("task_graph", True, "run_task_graph")
        _refresh_working()
        return s

    def h_multi_agent(s: dict) -> dict:
        _progress(5, "\U0001f91d Multi-Agent")
        result = run_legacy_multi_agent_workflow(
            task=task,
            model_name=model_name,
            memory_profile=memory_profile,
            num_ctx=num_ctx, progress_callback=None,
        )
        s["multi_agent_result"] = result
        s["answer"] = (result or {}).get("final", "") or s.get("answer", "")
        if result:
            _wm("multi_agent", "finding", str(result)[:3000], score=0.92)
        _record_tool("multi_agent", True, "run_multi_agent")
        _refresh_working()
        return s

    def h_self_improve(s: dict) -> dict:
        _progress(5, "\u267b\ufe0f Self-Improving")
        result = run_self_improving_agent(
            task, model_name, memory_profile,
            num_ctx=num_ctx, max_iters=2,
            progress_callback=None,
            base_force_strategy="direct",
        )
        s["self_improve_result"] = result
        s["answer"] = (result or {}).get("answer", "") or s.get("answer", "")
        if result:
            _wm("self_improve", "finding", str(result)[:3000], score=0.9)
        _record_tool("self_improve", True, "run_self_improving_agent")
        _refresh_working()
        return s

    def h_reflection_v2(s: dict) -> dict:
        _progress(6, "\U0001faa9 Reflection v2")
        if s.get("selected_strategy") == "self_improve" and s.get("answer", "").strip():
            return s
        refl = reflection_v2(
            task=task,
            answer=s.get("answer", ""),
            model_name=model_name,
            memory_context=s.get("memory_context", ""),
            kb_context="\n\n".join(
                x for x in [s.get("kb_context", ""), s.get("working_context", "")]
                if x.strip()
            ),
            profile_name=memory_profile,
            num_ctx=num_ctx,
        )
        false_count = count_false_flags(refl)
        if false_count >= 3 or refl.get("needs_retry"):
            regenerated = regenerate_answer_from_context(
                task=task, model_name=model_name,
                memory_context="\n\n".join(
                    x for x in [s.get("memory_context", ""), s.get("working_context", "")]
                    if x.strip()
                ),
                kb_context=s.get("kb_context", ""),
                prior_answer=s.get("answer", ""),
                reflection_notes=refl.get("notes", ""),
                num_ctx=num_ctx,
            )
            s["answer"] = regenerated
            refl["regenerated"] = True
        else:
            improved = refl.get("improved_answer", "").strip()
            if improved:
                s["answer"] = improved
            refl["regenerated"] = False
        s["reflection"] = refl
        _wm("reflection_v2", "decision", json.dumps(refl, ensure_ascii=False)[:2500], score=0.9)
        if s.get("answer", "").strip():
            _wm("reflection_v2", "finding", s["answer"][:2500], score=0.8)
        _refresh_working()
        return s

    def h_finalize(s: dict) -> dict:
        _progress(total_steps, "\u2705 Final")
        if not s.get("answer", "").strip():
            fallback_prompt = (
                "\u0421\u043e\u0431\u0435\u0440\u0438 \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442 \u043f\u043e \u0437\u0430\u0434\u0430\u0447\u0435.\n\n"
                f"\u0417\u0430\u0434\u0430\u0447\u0430:\n{task}\n\n"
                f"\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u043f\u0430\u043c\u044f\u0442\u0438:\n{s.get('memory_context', '')[:5000]}\n\n"
                f"\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 KB:\n{s.get('kb_context', '')[:4000]}\n\n"
                f"\u0420\u0430\u0431\u043e\u0447\u0430\u044f \u043f\u0430\u043c\u044f\u0442\u044c:\n{s.get('working_context', '')[:4000]}\n\n"
                f"\u041f\u043e\u0434\u0441\u043a\u0430\u0437\u043a\u0430 \u043f\u043e \u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442\u0430\u043c:\n{s.get('tool_hint', '')[:1500]}\n\n"
                "\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f:\n"
                "- \u043e\u0442\u0432\u0435\u0442 \u0434\u043e\u043b\u0436\u0435\u043d \u0431\u044b\u0442\u044c \u043a\u043e\u043d\u043a\u0440\u0435\u0442\u043d\u044b\u043c,\n"
                "- \u043d\u0435 \u0432\u044b\u0434\u0443\u043c\u044b\u0432\u0430\u0439 \u0444\u0430\u043a\u0442\u044b,\n"
                "- \u0435\u0441\u043b\u0438 \u0434\u0430\u043d\u043d\u044b\u0445 \u043c\u0430\u043b\u043e, \u0442\u0430\u043a \u0438 \u0441\u043a\u0430\u0436\u0438,\n"
                "- \u0434\u0430\u0439 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 \u043f\u0440\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0448\u0430\u0433."
            )
            s["answer"] = ask_model(
                model_name=model_name,
                profile_name="\u041e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
                user_input=fallback_prompt,
                memory_context="\n\n".join(
                    x for x in [s.get("memory_context", ""), s.get("working_context", "")]
                    if x.strip()
                ),
                use_memory=True,
                include_history=False,
                temp=0.15,
                num_ctx=num_ctx,
            )
        if s.get("answer", "").strip():
            _wm("finalize", "finding", s["answer"][:3000], score=0.95)
        _refresh_working()
        return s

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

    state = run_graph_with_retry_v8(graph, handlers, state, max_retries=2)

    status = "ok" if not state.get("failed_node") else "failed"
    try:
        record_task_run(
            task_text=task, route_mode=mode,
            graph_used=" -> ".join(graph),
            final_status=status, profile_name=memory_profile,
        )
    except Exception:
        pass

    latency = round(time.time() - run_started, 3)
    reflection = state.get("reflection", {}) or {}
    answer_ok = bool(state.get("answer", "").strip()) and not state.get("failed_node")
    quality_score = compute_reflection_quality_score(reflection)
    try:
        record_v8_strategy_usage(
            strategy=selected_strategy, route_mode=mode,
            task_hint=task, ok=answer_ok,
            score=round(quality_score, 3), latency=latency,
            notes=str(strategy.get("reason", ""))[:1000],
            profile_name=memory_profile,
        )
    except Exception:
        pass

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
    from app.domain.memory.knowledge_base import record_tool_usage

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
        working_context=base.get("working_context", "") or "",
        progress_callback=lambda idx, label: _progress(
            min(idx + 1, total_steps),
            label,
        ),
    )
    answer = loop_result.get("answer", "")
    reflection: dict | Any = loop_result.get("reflection", {}) or {}
    iterations = loop_result.get("iterations", [])
    working_context = loop_result.get("working_context", "") or ""

    try:
        record_tool_usage(
            tool_name="self_improving_agent",
            task_hint=task,
            ok=bool(answer.strip()),
            score=1.5 if answer.strip() else 0.0,
            notes=f"iterations={len(iterations)}",
            profile_name=memory_profile,
        )
    except Exception:
        pass

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
        working_context=working_context,
        persona_meta=persona_meta,
    )
