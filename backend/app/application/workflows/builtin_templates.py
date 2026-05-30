"""Builtin workflow template definitions.

Extracted from engine.py — pure data module.  engine.py imports
``get_builtin_workflow_templates`` and calls it once during seeding.
"""
from __future__ import annotations

from typing import Any

from app.application.workflows.workflow_ids import (
    MULTI_AGENT_DEFAULT_WORKFLOW_ID,
    MULTI_AGENT_FULL_WORKFLOW_ID,
    MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID,
    MULTI_AGENT_REFLECTION_WORKFLOW_ID,
)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_RESEARCH_PROMPT = (
    "Исходный запрос:\n{query}\n\n"
    "Дополнительный контекст:\n{context}\n\n"
    "План оркестратора:\n{plan}\n\n"
    "Память:\n{memory_context}\n\n"
    "Сделай исследовательскую часть: ключевые факты, ограничения, риски и полезные направления."
)
_PROGRAMMER_PROMPT = (
    "Задача:\n{query}\n\n"
    "Контекст:\n{context}\n\n"
    "План:\n{plan}\n\n"
    "Исследование:\n{research}\n\n"
    "Контекст проекта:\n{project_context}\n\n"
    "Контекст файлов:\n{file_context}\n\n"
    "Подготовь техническое решение, кодовый подход или реализационный план."
)
_ANALYST_PROMPT = (
    "Задача:\n{query}\n\n"
    "План:\n{plan}\n\n"
    "Исследование:\n{research}\n\n"
    "Техническое решение:\n{coding}\n\n"
    "Сделай аналитический вывод: риски, слабые места, рекомендации и next steps."
)
_ORCHESTRATOR_PLAN_PROMPT = (
    "Ты работаешь как оркестратор многошагового пайплайна.\n\n"
    "Запрос:\n{query}\n\n"
    "Контекст:\n{context}\n\n"
    "Верни компактный план выполнения: что исследовать, что реализовать и что проверить."
)
_FINAL_PROMPT = (
    "Собери финальный deliverable по запросу.\n\n"
    "Запрос:\n{query}\n\n"
    "План:\n{plan}\n\n"
    "Исследование:\n{research}\n\n"
    "Техническое решение:\n{coding}\n\n"
    "Анализ:\n{analysis}\n\n"
    "Сделай финальный связный ответ с кратким выводом, основной частью и практическими следующими шагами."
)
_REFLECTION_PROMPT = (
    "Проверь итоговый ответ как reviewer.\n\n"
    "Запрос:\n{query}\n\n"
    "Финальный ответ:\n{final}\n\n"
    "Укажи, что в нём хорошо, что слабо, и что нужно улучшить."
)

# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------

_DEFAULT_STEPS: list[dict[str, Any]] = [
    {
        "id": "research",
        "type": "agent",
        "agent_id": "builtin-researcher",
        "input_map": {
            "query": "$.input.query",
            "context": "$.input.context",
            "plan": "$.input.plan",
            "memory_context": "$.context.memory_context",
        },
        "save_as": "research",
        "next": "coding",
        "config": {"profile_name": "Исследователь", "prompt_template": _RESEARCH_PROMPT, "label": "Research"},
    },
    {
        "id": "coding",
        "type": "agent",
        "agent_id": "builtin-programmer",
        "input_map": {
            "query": "$.input.query",
            "context": "$.input.context",
            "plan": "$.input.plan",
            "research": "$.steps.research.answer",
            "project_context": "$.input.project_context",
            "file_context": "$.input.file_context",
        },
        "save_as": "coding",
        "next": "analysis",
        "config": {"profile_name": "Программист", "prompt_template": _PROGRAMMER_PROMPT, "label": "Coding"},
    },
    {
        "id": "analysis",
        "type": "agent",
        "agent_id": "builtin-analyst",
        "input_map": {
            "query": "$.input.query",
            "plan": "$.input.plan",
            "research": "$.steps.research.answer",
            "coding": "$.steps.coding.answer",
        },
        "save_as": "analysis",
        "next": None,
        "config": {"profile_name": "Аналитик", "prompt_template": _ANALYST_PROMPT, "label": "Analysis"},
    },
]

_ORCHESTRATED_STEPS: list[dict[str, Any]] = [
    {
        "id": "plan",
        "type": "agent",
        "agent_id": "builtin-orchestrator",
        "input_map": {"query": "$.input.query", "context": "$.input.context"},
        "save_as": "plan",
        "next": "research",
        "config": {"profile_name": "Универсальный", "prompt_template": _ORCHESTRATOR_PLAN_PROMPT, "label": "Plan"},
    },
    {
        "id": "research",
        "type": "agent",
        "agent_id": "builtin-researcher",
        "input_map": {
            "query": "$.input.query",
            "context": "$.input.context",
            "plan": "$.steps.plan.answer",
            "memory_context": "$.context.memory_context",
        },
        "save_as": "research",
        "next": "coding",
        "config": {"profile_name": "Исследователь", "prompt_template": _RESEARCH_PROMPT, "label": "Research"},
    },
    {
        "id": "coding",
        "type": "agent",
        "agent_id": "builtin-programmer",
        "input_map": {
            "query": "$.input.query",
            "context": "$.input.context",
            "plan": "$.steps.plan.answer",
            "research": "$.steps.research.answer",
            "project_context": "$.input.project_context",
            "file_context": "$.input.file_context",
        },
        "save_as": "coding",
        "next": "analysis",
        "config": {"profile_name": "Программист", "prompt_template": _PROGRAMMER_PROMPT, "label": "Coding"},
    },
    {
        "id": "analysis",
        "type": "agent",
        "agent_id": "builtin-analyst",
        "input_map": {
            "query": "$.input.query",
            "plan": "$.steps.plan.answer",
            "research": "$.steps.research.answer",
            "coding": "$.steps.coding.answer",
        },
        "save_as": "analysis",
        "next": "final",
        "config": {"profile_name": "Аналитик", "prompt_template": _ANALYST_PROMPT, "label": "Analysis"},
    },
    {
        "id": "final",
        "type": "agent",
        "agent_id": "builtin-orchestrator",
        "input_map": {
            "query": "$.input.query",
            "plan": "$.steps.plan.answer",
            "research": "$.steps.research.answer",
            "coding": "$.steps.coding.answer",
            "analysis": "$.steps.analysis.answer",
        },
        "save_as": "final",
        "next": None,
        "config": {"profile_name": "Универсальный", "prompt_template": _FINAL_PROMPT, "label": "Final"},
    },
]


def _with_reflection(steps: list[dict[str, Any]], final_step_id: str) -> list[dict[str, Any]]:
    """Append a reflection step to a copy of *steps*, chaining from *final_step_id*."""
    result = [
        {**s, "input_map": dict(s.get("input_map", {})), "config": dict(s.get("config", {}))}
        for s in steps
    ]
    # Wire the last "real" step to reflection
    for s in result:
        if s["id"] == final_step_id:
            s["next"] = "reflection"
    result.append(
        {
            "id": "reflection",
            "type": "agent",
            "agent_id": "builtin-reviewer",
            "input_map": {"query": "$.input.query", "final": f"$.steps.{final_step_id}.answer"},
            "save_as": "reflection",
            "next": None,
            "config": {"profile_name": "Аналитик", "prompt_template": _REFLECTION_PROMPT, "label": "Reflection"},
        }
    )
    return result


def _template(
    workflow_id: str,
    *,
    name: str,
    name_ru: str,
    description: str,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": workflow_id,
        "name": name,
        "name_ru": name_ru,
        "description": description,
        "description_ru": description,
        "graph": {"entry_step": steps[0]["id"], "steps": steps},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "enabled": True,
        "version": 1,
        "source": "builtin",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_builtin_workflow_templates() -> list[dict[str, Any]]:
    """Return the four builtin multi-agent workflow template dicts."""
    reflection_steps = _with_reflection(_DEFAULT_STEPS, "analysis")
    full_steps = _with_reflection(_ORCHESTRATED_STEPS, "final")

    return [
        _template(
            MULTI_AGENT_DEFAULT_WORKFLOW_ID,
            name="Multi-agent default",
            name_ru="Базовый мультиагентный workflow",
            description="Исследователь -> Программист -> Аналитик",
            steps=_DEFAULT_STEPS,
        ),
        _template(
            MULTI_AGENT_REFLECTION_WORKFLOW_ID,
            name="Multi-agent reflection",
            name_ru="Мультиагентный workflow с рефлексией",
            description="Исследователь -> Программист -> Аналитик -> Reflection",
            steps=reflection_steps,
        ),
        _template(
            MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID,
            name="Multi-agent orchestrated",
            name_ru="Оркестрированный мультиагентный workflow",
            description="Plan -> Research -> Coding -> Analysis -> Final",
            steps=_ORCHESTRATED_STEPS,
        ),
        _template(
            MULTI_AGENT_FULL_WORKFLOW_ID,
            name="Multi-agent full",
            name_ru="Полный мультиагентный workflow",
            description="Plan -> Research -> Coding -> Analysis -> Final -> Reflection",
            steps=full_steps,
        ),
    ]
