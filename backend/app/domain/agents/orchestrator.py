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

from app.core.llm import ask_model, clean_code_fence, safe_json_parse

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
    # Lazy imports to avoid circular deps
    from app.core.agents import (
        route_task,
        choose_v8_strategy,
        run_planner_agent,
        run_task_graph,
        run_multi_agent,
    )
    from app.core.memory import (
        add_working_memory,
        build_kb_context,
        build_memory_context,
        build_working_memory_context,
        get_tool_preferences,
        record_task_run,
        record_tool_usage,
        record_v8_strategy_usage,
    )
    from app.domain.agents.reflection import (
        reflection_v2,
        count_false_flags,
        regenerate_answer_from_context,
        run_graph_with_retry_v8,
    )
    from app.domain.agents.router import TASK_GRAPH_TEMPLATES_V8

    run_started = time.time()
    run_id = uuid4().hex[:12]
    route = route_task(
        task, model_name=model_name,
        memory_profile=memory_profile, num_ctx=num_ctx,
    )
    if not isinstance(route, dict):
        route = {
            "mode": "chat",
            "agent": "chat_agent",
            "use_graph": False,
            "confidence": 0.0,
            "source": "fallback",
            "reason": "route_task returned None or invalid data",
        }

    mode = route.get("mode", "chat") or "chat"
    strategy = choose_v8_strategy(
        task=task, route=route, model_name=model_name,
        memory_profile=memory_profile, num_ctx=num_ctx,
        force_strategy=force_strategy,
    )
    selected_strategy = strategy.get("strategy", "direct") or "direct"

    graph_map = {
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
    graph = (
        graph_map.get(selected_strategy)
        or TASK_GRAPH_TEMPLATES_V8.get(
            mode,
            ["retrieve_memory", "retrieve_working_memory", "finalize"],
        )
    )
    total_steps = max(len(graph), 1)

    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

    state: Dict[str, Any] = {
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
        _progress(1, "\U0001f9e0 \u041f\u0430\u043c\u044f\u0442\u044c")
        s["memory_context"] = build_memory_context(task, memory_profile, top_k=8)
        if s["memory_context"].strip():
            _wm("retrieve_memory", "finding", s["memory_context"][:2000], score=0.9)
        _refresh_working()
        return s

    def h_retrieve_kb(s: dict) -> dict:
        _progress(2, "\U0001f4da KB")
        s["kb_context"] = build_kb_context(task, profile_name=memory_profile, top_k=4)
        if s["kb_context"].strip():
            _wm("retrieve_kb", "source", s["kb_context"][:2000], score=0.85)
        _refresh_working()
        return s

    def h_retrieve_working_memory(s: dict) -> dict:
        _progress(3, "\U0001f9e9 Working memory")
        _refresh_working()
        return s

    def h_tool_hint(s: dict) -> dict:
        _progress(4, "\U0001f6e0 Tool memory")
        try:
            prefs = get_tool_preferences(task, profile_name=memory_profile, limit=3)
        except Exception:
            prefs = []
        if prefs:
            lines = []
            for p in prefs:
                tool = p.get("tool", p.get("tool_name", "unknown"))
                success_rate = p.get("success_rate")
                if success_rate is None:
                    runs = max(int(p.get("runs", 0) or p.get("uses", 0) or 0), 1)
                    success_rate = round(float(p.get("success", 0)) / runs, 2)
                uses = p.get("uses", p.get("runs", 0))
                lines.append(f"- {tool}: success_rate={success_rate}, uses={uses}")
            s["tool_hint"] = (
                "\u041f\u0440\u0435\u0434\u043f\u043e\u0447\u0442\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0435 \u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442\u044b \u043f\u043e \u043f\u0440\u043e\u0448\u043b\u043e\u043c\u0443 \u043e\u043f\u044b\u0442\u0443:\n"
                + "\n".join(lines)
            )
            _wm("tool_hint", "decision", s["tool_hint"][:1800], score=0.75)
        else:
            s["tool_hint"] = ""
        _refresh_working()
        return s

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
        result = run_multi_agent(
            task, model_name, memory_profile,
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
    quality_score = 1.0
    if reflection:
        quality_score = (
            0.2
            + 0.2 * float(bool(reflection.get("answered", True)))
            + 0.2 * float(bool(reflection.get("grounded", True)))
            + 0.2 * float(bool(reflection.get("complete", True)))
            + 0.2 * float(bool(reflection.get("actionable", True)))
        )
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

    try:
        from app.services.persona_service import observe_dialogue
        persona_meta = observe_dialogue(
            dialog_id=run_id, session_id=run_id,
            profile_name=memory_profile, model_name=model_name,
            user_input=task,
            answer_text=state.get("answer", ""),
            route=mode, reflection=reflection,
            outcome_ok=answer_ok,
        )
    except Exception:
        persona_meta = None

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
    from app.core.memory import (
        build_memory_context,
        build_kb_context,
        build_working_memory_context,
        record_self_improve_run,
        record_tool_usage,
    )
    from app.domain.agents.reflection import reflection_v2

    total_steps = max(2, int(max_iters) + 1)

    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

    _progress(1, "\U0001f680 \u0411\u0430\u0437\u043e\u0432\u044b\u0439 \u0437\u0430\u043f\u0443\u0441\u043a V8")
    base = run_agent_v8(
        task=task, model_name=model_name,
        memory_profile=memory_profile, num_ctx=num_ctx,
        progress_callback=None,
        force_strategy=base_force_strategy,
    )

    answer = (base.get("answer", "") or "").strip()
    reflection: dict | Any = base.get("reflection", {}) or {}
    iterations: List[Dict[str, Any]] = []
    run_id = base.get("run_id", "")
    working_context = base.get("working_context", "") or ""

    for idx in range(1, max(0, int(max_iters)) + 1):
        _progress(min(idx + 1, total_steps), f"\U0001faa9 Self-Improve {idx}")
        mem_ctx = build_memory_context(task, memory_profile, top_k=8)
        kb_ctx = build_kb_context(task, profile_name=memory_profile, top_k=4)
        if run_id:
            try:
                working_context = build_working_memory_context(
                    run_id, profile_name=memory_profile, limit=12,
                )
            except Exception:
                pass

        combined_context = (
            (mem_ctx or "") + "\n\n"
            + (kb_ctx or "") + "\n\n"
            + (working_context or "")
        )
        critique_prompt = (
            "\u0422\u044b self-improve critic.\n"
            "\u0412\u0435\u0440\u043d\u0438 \u0422\u041e\u041b\u042c\u041a\u041e JSON:\n"
            "{\n"
            '  "improve": true,\n'
            '  "score": 0.0,\n'
            '  "issues": ["..."],\n'
            '  "focus": "\u0447\u0442\u043e \u0443\u043b\u0443\u0447\u0448\u0438\u0442\u044c"\n'
            "}\n\n"
            f"\u0417\u0410\u0414\u0410\u0427\u0410:\n{task}\n\n"
            f"\u0422\u0415\u041a\u0423\u0429\u0418\u0419 \u041e\u0422\u0412\u0415\u0422:\n{answer[:9000]}\n\n"
            f"REFLECTION:\n{json.dumps(reflection, ensure_ascii=False)}\n\n"
            f"\u041a\u041e\u041d\u0422\u0415\u041a\u0421\u0422:\n{combined_context[:9000]}"
        )
        raw_crit = ask_model(
            model_name=model_name,
            profile_name="\u0410\u043d\u0430\u043b\u0438\u0442\u0438\u043a",
            user_input=critique_prompt,
            memory_context=mem_ctx,
            use_memory=True,
            include_history=False,
            temp=0.05,
            num_ctx=min(num_ctx, 4096),
        )
        crit = safe_json_parse(clean_code_fence(raw_crit)) or {}
        should_improve = bool(crit.get("improve", idx == 1))
        if isinstance(reflection, dict) and (
            reflection.get("needs_retry")
            or not reflection.get("complete", True)
        ):
            should_improve = True

        if not should_improve:
            item = {
                "iteration": idx,
                "changed": False,
                "answer": answer,
                "critique": crit,
                "reflection": reflection,
            }
            iterations.append(item)
            try:
                record_self_improve_run(task, idx, answer, crit, reflection, memory_profile)
            except Exception:
                pass
            break

        improve_prompt = (
            "\u0423\u043b\u0443\u0447\u0448\u0438 \u043e\u0442\u0432\u0435\u0442 \u043f\u043e\u0441\u043b\u0435 self-improving loop.\n\n"
            f"\u0418\u0441\u0445\u043e\u0434\u043d\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430:\n{task}\n\n"
            f"\u0422\u0435\u043a\u0443\u0449\u0438\u0439 \u043e\u0442\u0432\u0435\u0442:\n{answer[:9000]}\n\n"
            f"\u041f\u0440\u043e\u0431\u043b\u0435\u043c\u044b / focus:\n{json.dumps(crit, ensure_ascii=False, indent=2)}\n\n"
            f"Reflection:\n{json.dumps(reflection, ensure_ascii=False, indent=2)}\n\n"
            f"\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u043f\u0430\u043c\u044f\u0442\u0438:\n{mem_ctx[:4000]}\n\n"
            f"\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 KB:\n{kb_ctx[:3000]}\n\n"
            f"\u0420\u0430\u0431\u043e\u0447\u0430\u044f \u043f\u0430\u043c\u044f\u0442\u044c:\n{working_context[:3000]}\n\n"
            "\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f:\n"
            "- \u0421\u0434\u0435\u043b\u0430\u0439 \u043e\u0442\u0432\u0435\u0442 \u0442\u043e\u0447\u043d\u0435\u0435 \u0438 \u043f\u0440\u0430\u043a\u0442\u0438\u0447\u043d\u0435\u0435.\n"
            "- \u041d\u0435 \u0432\u044b\u0434\u0443\u043c\u044b\u0432\u0430\u0439 \u0444\u0430\u043a\u0442\u044b.\n"
            "- \u0415\u0441\u043b\u0438 \u0434\u0430\u043d\u043d\u044b\u0445 \u043d\u0435 \u0445\u0432\u0430\u0442\u0430\u0435\u0442 \u2014 \u0441\u043a\u0430\u0436\u0438 \u044d\u0442\u043e \u044f\u0432\u043d\u043e.\n"
            "- \u0421\u043e\u0445\u0440\u0430\u043d\u0438 \u0441\u0438\u043b\u044c\u043d\u044b\u0435 \u0447\u0430\u0441\u0442\u0438 \u043f\u0440\u043e\u0448\u043b\u043e\u0433\u043e \u043e\u0442\u0432\u0435\u0442\u0430."
        )
        improved = ask_model(
            model_name=model_name,
            profile_name="\u041e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
            user_input=improve_prompt,
            memory_context="\n\n".join(
                x for x in [mem_ctx, kb_ctx, working_context] if x.strip()
            ),
            use_memory=True,
            include_history=False,
            temp=0.15,
            num_ctx=num_ctx,
        ).strip() or answer

        reflection = reflection_v2(
            task=task, answer=improved,
            model_name=model_name,
            memory_context="\n\n".join(
                x for x in [mem_ctx, working_context] if x.strip()
            ),
            kb_context=kb_ctx,
            profile_name=memory_profile,
            num_ctx=num_ctx,
        )
        answer = improved
        item = {
            "iteration": idx,
            "changed": True,
            "answer": answer,
            "critique": crit,
            "reflection": reflection,
        }
        iterations.append(item)
        try:
            record_self_improve_run(task, idx, answer, crit, reflection, memory_profile)
        except Exception:
            pass

        if (
            isinstance(reflection, dict)
            and reflection.get("complete", True)
            and reflection.get("answered", True)
            and not reflection.get("needs_retry", False)
        ):
            break

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

    try:
        from app.services.persona_service import observe_dialogue
        persona_meta = observe_dialogue(
            dialog_id=run_id or f"self-improve-{memory_profile}",
            session_id=run_id or f"self-improve-{memory_profile}",
            profile_name=memory_profile, model_name=model_name,
            user_input=task, answer_text=answer,
            route="self_improve",
            reflection=reflection if isinstance(reflection, dict) else {},
            outcome_ok=bool(answer.strip()),
        )
    except Exception:
        persona_meta = None

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
