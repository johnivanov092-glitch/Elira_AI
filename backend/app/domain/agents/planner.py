"""Planner and task-graph orchestration.

Extracted from core/agents.py. This module now keeps plan/graph creation,
normalization, and final synthesis while execution/runtime helpers live in
planner_runtime.py.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List
from urllib.parse import quote_plus

from app.core.llm import ask_model, clean_code_fence, safe_json_parse
from app.domain.agents.planner_runtime import (
    build_task_graph_state_blob,
    execute_planner_step,
    execute_task_graph_node,
    extract_first_url,
    planner_safe_terminal_command,
    retry_failed_task_graph_steps,
)
from app.domain.agents.reflection import reflect_and_improve_answer


ProgressCallback = Callable[[int, int, str], None] | None


_SEARCH_KEYWORDS = [
    "найди", "поиск",
    "веб", "сайт",
    "документац",
    "интернет",
    "страниц",
]

_MEMORY_KEYWORDS = [
    "вспомни",
    "память",
    "что ты знаешь",
    "из памяти",
    "memory",
]


def planner_default_steps(task: str) -> List[dict]:
    url = extract_first_url(task)
    steps: List[dict] = []
    if url:
        steps.append({
            "tool": "browser",
            "goal": "Прочитай страницу и извлеки факты, полезные для исходной задачи.",
            "url": url,
        })
    elif any(word in task.lower() for word in _SEARCH_KEYWORDS):
        steps.append({
            "tool": "browser",
            "goal": task[:400],
            "url": f"https://duckduckgo.com/?q={quote_plus(task[:200])}",
        })
    steps.append({
        "tool": "reasoning",
        "goal": "Собери вывод и практические шаги на основе доступного контекста.",
    })
    return steps[:4]


def task_graph_default(task: str) -> List[dict]:
    url = extract_first_url(task)
    nodes: List[dict] = []

    if any(word in task.lower() for word in _MEMORY_KEYWORDS):
        nodes.append({
            "id": "n1",
            "tool": "memory_lookup",
            "goal": task[:400],
            "depends_on": [],
        })

    if url:
        nodes.append({
            "id": f"n{len(nodes) + 1}",
            "tool": "browser",
            "goal": "Прочитай страницу и извлеки факты, полезные для исходной задачи.",
            "url": url,
            "depends_on": [],
        })
    elif any(word in task.lower() for word in _SEARCH_KEYWORDS):
        nodes.append({
            "id": f"n{len(nodes) + 1}",
            "tool": "browser",
            "goal": task[:400],
            "url": f"https://duckduckgo.com/?q={quote_plus(task[:200])}",
            "depends_on": [],
        })

    nodes.append({
        "id": f"n{len(nodes) + 1}",
        "tool": "reasoning",
        "goal": "Собери финальный аналитический ответ по исходной задаче.",
        "depends_on": [n["id"] for n in nodes],
    })
    return nodes[:6]


def normalize_task_graph(raw_graph: Any, task: str) -> List[dict]:
    if not isinstance(raw_graph, list):
        return task_graph_default(task)

    cleaned: List[dict] = []
    seen_ids: set[str] = set()

    for idx, item in enumerate(raw_graph[:6], start=1):
        if not isinstance(item, dict):
            continue

        node_id = str(item.get("id", "")).strip() or f"n{idx}"
        if node_id in seen_ids:
            node_id = f"n{idx}"
        seen_ids.add(node_id)

        tool = str(item.get("tool", "")).strip().lower()
        if tool not in {"browser", "terminal", "reasoning", "memory_lookup"}:
            continue

        deps = item.get("depends_on", [])
        if not isinstance(deps, list):
            deps = []
        deps = [str(d).strip() for d in deps if str(d).strip() and str(d).strip() != node_id]

        cleaned.append({
            "id": node_id,
            "tool": tool,
            "goal": str(item.get("goal", "")).strip() or task[:400],
            "url": str(item.get("url", "")).strip(),
            "command": str(item.get("command", "")).strip(),
            "depends_on": deps,
        })

    if not cleaned:
        return task_graph_default(task)

    if cleaned[-1]["tool"] != "reasoning":
        cleaned.append({
            "id": f"n{len(cleaned) + 1}",
            "tool": "reasoning",
            "goal": "Собери финальный аналитический ответ по исходной задаче.",
            "url": "",
            "command": "",
            "depends_on": [n["id"] for n in cleaned],
        })

    valid_ids = {n["id"] for n in cleaned}
    for node in cleaned:
        node["depends_on"] = [d for d in node["depends_on"] if d in valid_ids and d != node["id"]]

    return cleaned[:7]


def normalize_planner_steps(raw_plan: Any, task: str) -> List[dict]:
    """Parse LLM output into a validated list of planner steps."""
    if not isinstance(raw_plan, list):
        return planner_default_steps(task)

    normalized: List[dict] = []
    for item in raw_plan[:4]:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool", "")).strip().lower()
        if tool not in {"browser", "terminal", "reasoning"}:
            continue
        normalized.append({
            "tool": tool,
            "goal": str(item.get("goal", "")).strip() or task[:400],
            "url": str(item.get("url", "")).strip(),
            "command": str(item.get("command", "")).strip(),
        })
    if not normalized:
        return planner_default_steps(task)
    if normalized[-1]["tool"] != "reasoning":
        normalized.append({
            "tool": "reasoning",
            "goal": "Собери финальный ответ по всем наблюдениям.",
            "url": "",
            "command": "",
        })
    return normalized[:5]


def make_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
) -> List[dict]:
    from app.application.memory.context import build_default_memory_context

    memory_context = build_default_memory_context(
        query=task,
        profile_name=memory_profile,
        top_k=8,
    )
    planner_prompt = (
        "Ты строишь task graph для локальной AI-системы. "
        "Верни ТОЛЬКО JSON-массив узлов без пояснений. "
        "Каждый узел должен иметь формат:\n"
        '[{"id":"n1","tool":"browser|terminal|reasoning|memory_lookup",'
        '"goal":"...","url":"optional","command":"optional","depends_on":["n0"]}]\n\n'
        "Правила:\n"
        "- Узлы должны быть короткими и практичными.\n"
        "- browser: только если нужен веб/сайт/документация/страница.\n"
        "- terminal: только для безопасных read-only команд локального анализа.\n"
        "- memory_lookup: когда нужно поднять релевантную память профиля.\n"
        "- reasoning: для аналитики и синтеза.\n"
        "- Последний узел всегда reasoning.\n"
        "- Максимум 6 узлов.\n"
        "- Не придумывай опасные команды.\n\n"
        f"Задача:\n{task}"
    )
    raw_graph = ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
        user_input=planner_prompt,
        memory_context=memory_context,
        use_memory=True,
        temp=0.05,
        include_history=False,
        num_ctx=num_ctx,
    )
    raw_graph = clean_code_fence(re.sub(r"^```json\s*", "", (raw_graph or "").strip()))
    parsed = safe_json_parse(raw_graph)
    return normalize_task_graph(parsed, task)


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

    def _progress(step: int, label: str) -> None:
        if progress_callback:
            progress_callback(step, total_steps, label)

    memory_context = build_default_memory_context(
        query=task,
        profile_name=memory_profile,
        top_k=8,
    )

    _progress(1, "🧭 Planner: строю план...")
    planner_prompt = (
        "Ты Planner agent. Разбей задачу на 2-4 шага и верни ТОЛЬКО JSON-массив без пояснений.\n"
        "Формат шага:\n"
        '[{"tool":"browser|terminal|reasoning","goal":"...","url":"optional","command":"optional"}]\n'
        "Правила:\n"
        "- browser: только если нужен веб/страница/документация/поиск.\n"
        "- terminal: только для БЕЗОПАСНЫХ read-only команд локального анализа.\n"
        "- reasoning: финальный аналитический шаг.\n"
        "- Не предлагай опасные команды.\n"
        "- Если в задаче есть URL, используй его в browser step.\n"
        "- Последний шаг всегда reasoning.\n\n"
        f"Задача:\n{task}"
    )
    raw_plan = ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
        user_input=planner_prompt,
        memory_context=memory_context,
        use_memory=True,
        temp=0.05,
        include_history=False,
        num_ctx=num_ctx,
    )
    raw_plan = clean_code_fence(re.sub(r"^```json\s*", "", (raw_plan or "").strip()))
    plan = safe_json_parse(raw_plan)
    normalized_plan = normalize_planner_steps(plan, task)

    _progress(2, "🛠 Planner: выполняю шаги...")
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

    _progress(3, "🎯 Planner: собираю итог...")
    context_blob = "\n\n".join(gathered_contexts)[:24000]
    final_prompt = (
        "Ты Planner-Orchestrator. Собери финальный ответ пользователю на основе исходной задачи "
        "и результатов шагов. Если данных недостаточно, честно укажи это.\n\n"
        f"ЗАДАЧА:\n{task}\n\n"
        f"ПЛАН:\n{json.dumps(normalized_plan, ensure_ascii=False, indent=2)}\n\n"
        f"КОНТЕКСТ ШАГОВ:\n{context_blob}\n\n"
        "Структура:\n"
        "1. Краткий итог\n"
        "2. Что удалось узнать / сделать\n"
        "3. Практические следующие шаги"
    )
    final_draft = ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
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

    def _progress(step: int, label: str) -> None:
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
            _progress(step_idx, f"🕸 Выполняю {node['id']} · {node['tool']}")
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
                    "output": "Узел не выполнен: зависимости не разрешились или граф некорректен.",
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

    _progress(total_steps, "🎯 Task Graph: собираю итог...")
    state_blob = build_task_graph_state_blob(execution_log)

    final_prompt = (
        "Ты Task Graph Orchestrator. Собери финальный ответ пользователю на основе состояния графа. "
        "Честно отмечай, если какой-то узел не сработал или данных не хватило.\n\n"
        f"ИСХОДНАЯ ЗАДАЧА:\n{task}\n\n"
        f"ГРАФ:\n{json.dumps(graph, ensure_ascii=False, indent=2)}\n\n"
        f"STATE:\n{state_blob}\n\n"
        "Структура ответа:\n"
        "1. Краткий итог\n"
        "2. Что удалось узнать / сделать\n"
        "3. Ограничения или сбои\n"
        "4. Практические следующие шаги"
    )
    final_draft = ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
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
