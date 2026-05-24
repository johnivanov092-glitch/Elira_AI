"""Prompt builders for planner and task-graph agents."""
from __future__ import annotations

import json
from typing import Any, List


def build_task_graph_prompt(task: str) -> str:
    return (
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


def build_planner_plan_prompt(task: str) -> str:
    return (
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


def build_planner_summary_prompt(
    *,
    task: str,
    normalized_plan: List[dict],
    context_blob: str,
) -> str:
    return (
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


def build_task_graph_reasoning_prompt(
    *,
    task: str,
    node_goal: str,
    dep_context: str,
) -> str:
    return (
        "Ты reasoning-node в task graph. Выполни только задачу этого узла, "
        "опираясь на исходную задачу и контекст зависимостей. "
        "Если контекста мало — скажи об этом прямо.\n\n"
        f"ИСХОДНАЯ ЗАДАЧА:\n{task}\n\n"
        f"ЗАДАЧА УЗЛА:\n{node_goal}\n\n"
        "КОНТЕКСТ ЗАВИСИМОСТЕЙ:\n"
        + (dep_context or "Нет контекста зависимостей.")
    )


def build_task_graph_summary_prompt(
    *,
    task: str,
    graph: List[dict[str, Any]],
    state_blob: str,
) -> str:
    return (
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
