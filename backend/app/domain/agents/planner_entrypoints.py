"""Planner execution entrypoints."""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from app.core.llm import ask_model, clean_code_fence, safe_json_parse
from app.domain.agents.planner_graphs import make_task_graph, normalize_planner_steps
from app.domain.agents.planner_prompts import (
    build_planner_plan_prompt,
    build_planner_summary_prompt,
    build_task_graph_summary_prompt,
)
from app.domain.agents.planner_runtime import (
    build_task_graph_state_blob,
    execute_planner_step,
    execute_task_graph_node,
    retry_failed_task_graph_steps,
)
from app.domain.agents.reflection import reflect_and_improve_answer


ProgressCallback = Callable[[int, int, str], None] | None


def run_planner_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback: ProgressCallback = None,
) -> Dict[str, Any]:
    from app.application.memory.context import build_default_memory_context
    from app.domain.memory.knowledge_base import record_tool_usage

    total_steps = 3

    def progress(step: int, label: str) -> None:
        if progress_callback:
            progress_callback(step, total_steps, label)

    memory_context = build_default_memory_context(
        query=task,
        profile_name=memory_profile,
        top_k=8,
    )

    progress(1, "Planner: building plan")
    planner_prompt = build_planner_plan_prompt(task)
    raw_plan = ask_model(
        model_name=model_name,
        profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=planner_prompt,
        memory_context=memory_context,
        use_memory=True,
        temp=0.05,
        include_history=False,
        num_ctx=num_ctx,
    )
    raw_plan = clean_code_fence((raw_plan or "").strip())
    parsed = safe_json_parse(raw_plan)
    normalized_plan = normalize_planner_steps(parsed, task)

    progress(2, "Planner: executing steps")
    steps_log: List[dict] = []
    gathered_contexts: List[str] = []

    for idx, step in enumerate(normalized_plan, start=1):
        step_log, gathered_context = execute_planner_step(
            idx=idx,
            step=step,
            task=task,
            memory_profile=memory_profile,
        )
        steps_log.append(step_log)
        if gathered_context:
            gathered_contexts.append(gathered_context)

    progress(3, "Planner: assembling final answer")
    context_blob = "\n\n".join(gathered_contexts)[:24000]
    final_prompt = build_planner_summary_prompt(
        task=task,
        normalized_plan=normalized_plan,
        context_blob=context_blob,
    )
    final_draft = ask_model(
        model_name=model_name,
        profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=final_prompt,
        memory_context=memory_context,
        use_memory=True,
        include_history=False,
        num_ctx=num_ctx,
    )
    reflected = reflect_and_improve_answer(
        task,
        final_draft,
        model_name,
        extra_context=context_blob,
        num_ctx=num_ctx,
    )
    record_tool_usage(
        "planner_agent",
        task,
        True,
        score=1.2,
        notes="planner + execution + reflection",
        profile_name=memory_profile,
    )
    return {
        "plan": normalized_plan,
        "steps": steps_log,
        "final": reflected["final"],
        "reflection": reflected["critique"],
    }


def run_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback: ProgressCallback = None,
) -> Dict[str, Any]:
    from app.application.memory.context import build_default_memory_context
    from app.domain.memory.knowledge_base import record_tool_usage

    memory_context = build_default_memory_context(
        query=task,
        profile_name=memory_profile,
        top_k=8,
    )
    graph = make_task_graph(task, model_name, memory_profile, num_ctx=num_ctx)

    total_steps = max(len(graph) + 1, 2)

    def progress(step: int, label: str) -> None:
        if progress_callback:
            progress_callback(step, total_steps, label)

    node_results: Dict[str, dict] = {}
    execution_log: List[dict] = []
    remaining = list(graph)
    step_idx = 0

    while remaining:
        progressed = False
        for node in remaining[:]:
            deps = node.get("depends_on", [])
            if any(dep not in node_results for dep in deps):
                continue

            step_idx += 1
            progress(step_idx, f"Task Graph: {node['id']} · {node['tool']}")
            node_results[node["id"]] = execute_task_graph_node(
                task=task,
                node=node,
                model_name=model_name,
                memory_profile=memory_profile,
                memory_context=memory_context,
                node_results=node_results,
                num_ctx=num_ctx,
            )
            execution_log.append(node_results[node["id"]])
            remaining.remove(node)
            progressed = True

        if not progressed:
            for node in remaining:
                node_results[node["id"]] = {
                    "id": node["id"],
                    "tool": node["tool"],
                    "goal": node["goal"],
                    "ok": False,
                    "output": "РЈР·РµР» РЅРµ РІС‹РїРѕР»РЅРµРЅ: Р·Р°РІРёСЃРёРјРѕСЃС‚Рё РЅРµ СЂР°Р·СЂРµС€РёР»РёСЃСЊ РёР»Рё РіСЂР°С„ РЅРµРєРѕСЂСЂРµРєС‚РµРЅ.",
                }
                execution_log.append(node_results[node["id"]])
            break

    execution_log.extend(
        retry_failed_task_graph_steps(
            task=task,
            execution_log=execution_log,
            memory_profile=memory_profile,
        )
    )

    progress(total_steps, "Task Graph: assembling final answer")
    state_blob = build_task_graph_state_blob(execution_log)
    final_prompt = build_task_graph_summary_prompt(
        task=task,
        graph=graph,
        state_blob=state_blob,
    )
    final_draft = ask_model(
        model_name=model_name,
        profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=final_prompt,
        memory_context=memory_context,
        use_memory=True,
        include_history=False,
        num_ctx=num_ctx,
    )
    reflected = reflect_and_improve_answer(
        task,
        final_draft,
        model_name,
        extra_context=state_blob,
        num_ctx=num_ctx,
    )
    graph_ok = any(item.get("ok") for item in execution_log)
    record_tool_usage(
        "task_graph",
        task,
        graph_ok,
        score=1.3 if graph_ok else 0.6,
        notes="task graph + auto-retry + reflection",
        profile_name=memory_profile,
    )
    return {
        "graph": graph,
        "steps": execution_log,
        "final": reflected["final"],
        "reflection": reflected["critique"],
    }
