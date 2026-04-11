"""
Multi-agent workflow templates and orchestration.

Extracted from workflow_engine.py -- builtin workflow definitions,
seeding, multi-agent run orchestration, and legacy compatibility API.
"""
from __future__ import annotations

from typing import Any, Callable

# Workflow ID constants (canonical definitions, re-exported by workflow_engine)
MULTI_AGENT_DEFAULT_WORKFLOW_ID = "builtin.workflow.multi_agent.default"
MULTI_AGENT_REFLECTION_WORKFLOW_ID = "builtin.workflow.multi_agent.reflection"
MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID = "builtin.workflow.multi_agent.orchestrated"
MULTI_AGENT_FULL_WORKFLOW_ID = "builtin.workflow.multi_agent.full"

_BUILTIN_WORKFLOWS_SEEDED = False


def _multi_agent_template(
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


def _builtin_workflow_templates() -> list[dict[str, Any]]:
    research_prompt = (
        "Исходный запрос:\n{query}\n\n"
        "Дополнительный контекст:\n{context}\n\n"
        "План оркестратора:\n{plan}\n\n"
        "Память:\n{memory_context}\n\n"
        "Сделай исследовательскую часть: ключевые факты, ограничения, риски и полезные направления."
    )
    programmer_prompt = (
        "Задача:\n{query}\n\n"
        "Контекст:\n{context}\n\n"
        "План:\n{plan}\n\n"
        "Исследование:\n{research}\n\n"
        "Контекст проекта:\n{project_context}\n\n"
        "Контекст файлов:\n{file_context}\n\n"
        "Подготовь техническое решение, кодовый подход или реализационный план."
    )
    analyst_prompt = (
        "Задача:\n{query}\n\n"
        "План:\n{plan}\n\n"
        "Исследование:\n{research}\n\n"
        "Техническое решение:\n{coding}\n\n"
        "Сделай аналитический вывод: риски, слабые места, рекомендации и next steps."
    )
    orchestrator_plan_prompt = (
        "Ты работаешь как оркестратор многошагового пайплайна.\n\n"
        "Запрос:\n{query}\n\n"
        "Контекст:\n{context}\n\n"
        "Верни компактный план выполнения: что исследовать, что реализовать и что проверить."
    )
    final_prompt = (
        "Собери финальный deliverable по запросу.\n\n"
        "Запрос:\n{query}\n\n"
        "План:\n{plan}\n\n"
        "Исследование:\n{research}\n\n"
        "Техническое решение:\n{coding}\n\n"
        "Анализ:\n{analysis}\n\n"
        "Сделай финальный связный ответ с кратким выводом, основной частью и практическими следующими шагами."
    )
    reflection_prompt = (
        "Проверь итоговый ответ как reviewer.\n\n"
        "Запрос:\n{query}\n\n"
        "Финальный ответ:\n{final}\n\n"
        "Укажи, что в нём хорошо, что слабо, и что нужно улучшить."
    )

    default_steps = [
        {
            "id": "research",
            "type": "agent",
            "agent_id": "builtin-researcher",
            "input_map": {"query": "$.input.query", "context": "$.input.context", "plan": "$.input.plan", "memory_context": "$.context.memory_context"},
            "save_as": "research",
            "next": "coding",
            "config": {"profile_name": "Исследователь", "prompt_template": research_prompt, "label": "Research"},
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
            "config": {"profile_name": "Программист", "prompt_template": programmer_prompt, "label": "Coding"},
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
            "config": {"profile_name": "Аналитик", "prompt_template": analyst_prompt, "label": "Analysis"},
        },
    ]

    reflection_steps = [
        {**step, "input_map": dict(step.get("input_map", {})), "config": dict(step.get("config", {}))}
        for step in default_steps
    ]
    reflection_steps[2]["next"] = "reflection"
    reflection_steps.append(
        {
            "id": "reflection",
            "type": "agent",
            "agent_id": "builtin-reviewer",
            "input_map": {"query": "$.input.query", "final": "$.steps.analysis.answer"},
            "save_as": "reflection",
            "next": None,
            "config": {"profile_name": "Аналитик", "prompt_template": reflection_prompt, "label": "Reflection"},
        }
    )

    orchestrated_steps = [
        {
            "id": "plan",
            "type": "agent",
            "agent_id": "builtin-orchestrator",
            "input_map": {"query": "$.input.query", "context": "$.input.context"},
            "save_as": "plan",
            "next": "research",
            "config": {"profile_name": "Универсальный", "prompt_template": orchestrator_plan_prompt, "label": "Plan"},
        },
        {
            "id": "research",
            "type": "agent",
            "agent_id": "builtin-researcher",
            "input_map": {"query": "$.input.query", "context": "$.input.context", "plan": "$.steps.plan.answer", "memory_context": "$.context.memory_context"},
            "save_as": "research",
            "next": "coding",
            "config": {"profile_name": "Исследователь", "prompt_template": research_prompt, "label": "Research"},
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
            "config": {"profile_name": "Программист", "prompt_template": programmer_prompt, "label": "Coding"},
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
            "config": {"profile_name": "Аналитик", "prompt_template": analyst_prompt, "label": "Analysis"},
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
            "config": {"profile_name": "Универсальный", "prompt_template": final_prompt, "label": "Final"},
        },
    ]

    full_steps = [
        {**step, "input_map": dict(step.get("input_map", {})), "config": dict(step.get("config", {}))}
        for step in orchestrated_steps
    ]
    full_steps[-1]["next"] = "reflection"
    full_steps.append(
        {
            "id": "reflection",
            "type": "agent",
            "agent_id": "builtin-reviewer",
            "input_map": {"query": "$.input.query", "final": "$.steps.final.answer"},
            "save_as": "reflection",
            "next": None,
            "config": {"profile_name": "Аналитик", "prompt_template": reflection_prompt, "label": "Reflection"},
        }
    )

    return [
        _multi_agent_template(MULTI_AGENT_DEFAULT_WORKFLOW_ID, name="Multi-agent default", name_ru="Базовый мультиагентный workflow", description="Исследователь -> Программист -> Аналитик", steps=default_steps),
        _multi_agent_template(MULTI_AGENT_REFLECTION_WORKFLOW_ID, name="Multi-agent reflection", name_ru="Мультиагентный workflow с рефлексией", description="Исследователь -> Программист -> Аналитик -> Reflection", steps=reflection_steps),
        _multi_agent_template(MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID, name="Multi-agent orchestrated", name_ru="Оркестрированный мультиагентный workflow", description="Plan -> Research -> Coding -> Analysis -> Final", steps=orchestrated_steps),
        _multi_agent_template(MULTI_AGENT_FULL_WORKFLOW_ID, name="Multi-agent full", name_ru="Полный мультиагентный workflow", description="Plan -> Research -> Coding -> Analysis -> Final -> Reflection", steps=full_steps),
    ]


def seed_builtin_workflows() -> int:
    from app.services.workflow_engine import get_workflow_template, _upsert_workflow_template
    global _BUILTIN_WORKFLOWS_SEEDED
    if _BUILTIN_WORKFLOWS_SEEDED:
        return 0

    created = 0
    for template in _builtin_workflow_templates():
        existing = get_workflow_template(template["id"])
        _upsert_workflow_template(template)
        if not existing:
            created += 1

    _BUILTIN_WORKFLOWS_SEEDED = True
    return created


def _select_multi_agent_workflow_id(*, use_reflection: bool, use_orchestrator: bool) -> str:
    if use_orchestrator and use_reflection:
        return MULTI_AGENT_FULL_WORKFLOW_ID
    if use_orchestrator:
        return MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID
    if use_reflection:
        return MULTI_AGENT_REFLECTION_WORKFLOW_ID
    return MULTI_AGENT_DEFAULT_WORKFLOW_ID


def _step_answer(step_results: dict[str, Any], key: str) -> str:
    item = step_results.get(key, {}) if isinstance(step_results, dict) else {}
    if isinstance(item, dict):
        if "answer" in item:
            return str(item.get("answer", ""))
        output = item.get("output")
        if isinstance(output, dict):
            return str(output.get("answer", "") or output.get("result", ""))
    return str(item) if item else ""


def _build_multi_agent_timeline(template: dict[str, Any], step_results: dict[str, Any]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for step in template.get("graph", {}).get("steps", []):
        key = str(step.get("save_as") or step.get("id"))
        result = step_results.get(key)
        if not isinstance(result, dict):
            continue
        timeline.append(
            {
                "agent": step.get("id"),
                "status": "done" if result.get("ok") else "error",
                "label": str((step.get("config") or {}).get("label") or key),
                "length": len(_step_answer(step_results, key)),
            }
        )
    return timeline


def run_multi_agent_workflow(
    *,
    query: str,
    model_name: str = "qwen3:8b",
    context: str = "",
    agents: list[str] | None = None,
    use_reflection: bool = False,
    use_orchestrator: bool = False,
) -> dict[str, Any]:
    from app.services.workflow_engine import get_workflow_template, start_workflow_run
    seed_builtin_workflows()
    workflow_id = _select_multi_agent_workflow_id(use_reflection=use_reflection, use_orchestrator=use_orchestrator)
    run = start_workflow_run(
        workflow_id=workflow_id,
        workflow_input={"query": query, "context": context, "plan": "", "project_context": "", "file_context": ""},
        context={"model_name": model_name},
        trigger_source="advanced.multi_agent",
    )

    if run.get("status") != "completed":
        return {
            "ok": False,
            "error": run.get("error", {}).get("message", "Workflow failed"),
            "results": run.get("step_results", {}),
            "timeline": [],
            "agents_used": agents or ["researcher", "programmer", "analyst"],
            "orchestrator_used": use_orchestrator,
            "reflection_used": use_reflection,
            "workflow_run_id": run.get("run_id", ""),
            "workflow_id": workflow_id,
        }

    template = get_workflow_template(workflow_id) or {"graph": {"steps": []}}
    step_results = run.get("step_results", {})
    results: dict[str, str] = {}
    if "plan" in step_results:
        results["orchestrator"] = _step_answer(step_results, "plan")
    if "research" in step_results:
        results["researcher"] = _step_answer(step_results, "research")
    if "coding" in step_results:
        results["programmer"] = _step_answer(step_results, "coding")
    if "analysis" in step_results:
        results["analyst"] = _step_answer(step_results, "analysis")
    if "reflection" in step_results:
        results["reflection"] = _step_answer(step_results, "reflection")

    final_answer = _step_answer(step_results, "final")
    parts: list[str] = []
    if results.get("orchestrator"):
        parts.append(f"## План\n{results['orchestrator'][:2500]}")
    if results.get("researcher"):
        parts.append(f"## Исследование\n{results['researcher'][:2500]}")
    if results.get("programmer"):
        parts.append(f"## Техническое решение\n{results['programmer'][:2500]}")
    if results.get("analyst"):
        parts.append(f"## Анализ\n{results['analyst'][:2500]}")

    report = final_answer.strip() or "\n\n---\n\n".join(parts).strip()
    if results.get("reflection"):
        report = (report + f"\n\n---\n\n## Рефлексия\n{results['reflection'][:2500]}").strip()

    return {
        "ok": True,
        "report": report,
        "results": results,
        "timeline": _build_multi_agent_timeline(template, step_results),
        "agents_used": agents or ["researcher", "programmer", "analyst"],
        "orchestrator_used": use_orchestrator,
        "reflection_used": use_reflection,
        "workflow_run_id": run.get("run_id", ""),
        "workflow_id": workflow_id,
    }


def run_legacy_multi_agent_workflow(
    *,
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback: Callable[[int, int, str], None] | None = None,
    project_context: str = "",
    file_context: str = "",
) -> dict[str, Any]:
    from app.core.memory import build_memory_context
    from app.services.workflow_engine import start_workflow_run

    seed_builtin_workflows()
    memory_context = build_memory_context(task, memory_profile, top_k=5)
    run = start_workflow_run(
        workflow_id=MULTI_AGENT_FULL_WORKFLOW_ID,
        workflow_input={"query": task, "context": "", "project_context": project_context, "file_context": file_context},
        context={"model_name": model_name, "memory_context": memory_context, "num_ctx": num_ctx},
        trigger_source="core.multi_agent",
        progress_callback=progress_callback,
    )

    step_results = run.get("step_results", {})
    return {
        "plan": _step_answer(step_results, "plan"),
        "research": _step_answer(step_results, "research"),
        "coding": _step_answer(step_results, "coding"),
        "review": _step_answer(step_results, "analysis"),
        "final": _step_answer(step_results, "final") or _step_answer(step_results, "analysis"),
        "reflection": _step_answer(step_results, "reflection"),
    }
