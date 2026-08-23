"""
Multi-agent workflow templates and orchestration.

Extracted from workflow_engine.py -- builtin workflow definitions,
seeding, multi-agent run orchestration, and legacy compatibility API.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from app.application.workflows.db_path import get_workflow_db_path
from app.application.workflows.store import (
    get_workflow_run as _app_get_workflow_run,
    get_workflow_template as _app_get_workflow_template,
    init_db as _app_init_db,
    now_utc as _app_now_utc,
    upsert_workflow_template as _app_upsert_workflow_template,
)

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
        "Если нужного файла нет в контексте выше — прочитай его инструментом read_file "
        "(и при необходимости найди файлы через glob/grep), прежде чем предлагать правки.\n\n"
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
    global _BUILTIN_WORKFLOWS_SEEDED
    if _BUILTIN_WORKFLOWS_SEEDED:
        return 0

    workflow_db_path = get_workflow_db_path()
    _app_init_db(db_path=workflow_db_path)
    created = 0
    for template in _builtin_workflow_templates():
        existing = _app_get_workflow_template(db_path=workflow_db_path, workflow_id=template["id"])
        _app_upsert_workflow_template(db_path=workflow_db_path, template=template, now_func=_app_now_utc)
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


def _build_project_context_from_root(project_root: str | None) -> str:
    """Render a project overview (name + file list) for the selected folder.

    Mirrors context_builder._build_project_context_from_open_project, but scoped
    to an explicit root instead of the global open project — the multi-agent run
    operates on the folder the user picked in the Composer, which may differ from
    the chat's open project. Threading project_root into run_context already
    points the agents' file tools at this folder; this populates the prompt's
    {project_context} placeholder so the Coding step actually sees the file list.
    """
    if not project_root:
        return ""
    try:
        root = Path(project_root)
        if not root.exists():
            return ""
        file_list: list[str] = []
        for file_path in sorted(root.rglob("*"))[:50]:
            if not file_path.is_file():
                continue
            if any(blocked in str(file_path) for blocked in [".git", "node_modules", "__pycache__", ".venv", "dist"]):
                continue
            file_list.append(str(file_path.relative_to(root)))
        if not file_list:
            return ""
        return f"Открыт проект: {root.name}\nФайлы ({len(file_list)}):\n" + "\n".join("- " + item for item in file_list[:30])
    except Exception:
        return ""


# Directories we never scan when picking relevant files — same ignore set as the
# project-overview builder above and the code-agent's internal grep.
_RELEVANCE_BLOCKED_PARTS = (".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".mypy_cache")
# Russian/English stop-words and generic verbs that carry no targeting signal —
# keeping them would match nearly every file and drown out the real keywords.
_RELEVANCE_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "this", "that", "from", "into", "code", "file", "files",
        "что", "как", "для", "это", "эта", "или", "при", "под", "над", "над", "файл", "файлы",
        "код", "кода", "сделай", "сделать", "нужно", "надо", "проект", "проекта",
    }
)


def _looks_binary_bytes(raw: bytes) -> bool:
    """Cheap binary sniff: a NUL byte in the first 8 KiB means "don't read as text"."""
    return b"\x00" in raw[:8192]


def _query_keywords(query: str) -> list[str]:
    """Lower-cased word tokens (≥3 chars, not stop-words) used to score files."""
    import re

    tokens = re.findall(r"[0-9A-Za-zА-Яа-яЁё_]{3,}", (query or "").lower())
    seen: set[str] = set()
    keywords: list[str] = []
    for token in tokens:
        if token in _RELEVANCE_STOPWORDS or token in seen:
            continue
        seen.add(token)
        keywords.append(token)
    return keywords


def _build_file_context_from_root(
    project_root: str | None,
    query: str,
    *,
    max_files: int = 6,
    max_chars_per_file: int = 2500,
    max_scan_files: int = 2000,
) -> str:
    """Select files relevant to ``query`` from the disk project and return their
    *content* for the Coding step's {file_context} placeholder.

    Relevance is keyword/grep-style (no embedding index exists for the on-disk
    project): a file scores on query keywords appearing in its path (weighted) and
    in its body. The top ``max_files`` text files are returned, each truncated to
    ``max_chars_per_file``. Empty string when there's no root, no keywords, or no
    match — the prompt placeholder then renders blank, exactly as before.
    """
    if not project_root:
        return ""
    keywords = _query_keywords(query)
    if not keywords:
        return ""
    try:
        root = Path(project_root)
        if not root.exists():
            return ""

        scored: list[tuple[int, str, str]] = []  # (score, rel_path, text)
        scanned = 0
        for file_path in sorted(root.rglob("*")):
            if scanned >= max_scan_files:
                break
            if not file_path.is_file():
                continue
            if any(part in _RELEVANCE_BLOCKED_PARTS for part in file_path.parts):
                continue
            scanned += 1
            rel = str(file_path.relative_to(root)).replace("\\", "/")
            rel_lower = rel.lower()
            # Path matches are a strong signal — a filename hit is worth more than
            # an incidental body mention.
            score = sum(3 for kw in keywords if kw in rel_lower)
            try:
                raw = file_path.read_bytes()
            except Exception:
                continue
            if _looks_binary_bytes(raw):
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp1252", errors="replace")
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            body_lower = text.lower()
            score += sum(body_lower.count(kw) for kw in keywords)
            if score > 0:
                scored.append((score, rel, text))

        if not scored:
            return ""
        scored.sort(key=lambda item: (-item[0], item[1]))
        blocks: list[str] = []
        for _score, rel, text in scored[:max_files]:
            snippet = text[:max_chars_per_file]
            if len(text) > max_chars_per_file:
                snippet += "\n[... обрезано]"
            blocks.append(f"### {rel}\n```\n{snippet}\n```")
        return "Содержимое релевантных файлов:\n\n" + "\n\n".join(blocks)
    except Exception:
        return ""


def run_multi_agent_workflow(
    *,
    query: str,
    model_name: str = "local-model",
    context: str = "",
    agents: list[str] | None = None,
    use_reflection: bool = False,
    use_orchestrator: bool = False,
    project_root: str | None = None,
    num_ctx: int | None = None,
    permission_mode: str = "bypass",
    reasoning_effort: str = "none",
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    run_created_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    from app.application.workflows.runtime import cancel_workflow_run, start_workflow_run
    seed_builtin_workflows()
    workflow_id = _select_multi_agent_workflow_id(use_reflection=use_reflection, use_orchestrator=use_orchestrator)
    # The run context dict is threaded down to every agent step (see
    # step_executor._execute_agent_step); putting project_root/num_ctx here is
    # what scopes the agents' file tools to the user's selected folder and runs
    # them at the full production context window.
    run_context: dict[str, Any] = {"model_name": model_name}
    if project_root:
        run_context["project_root"] = project_root
    if isinstance(num_ctx, int) and num_ctx > 0:
        run_context["num_ctx"] = num_ctx
    run_context["reasoning_effort"] = reasoning_effort
    project_context = _build_project_context_from_root(project_root)
    file_context = _build_file_context_from_root(project_root, query)
    run = start_workflow_run(
        workflow_id=workflow_id,
        workflow_input={"query": query, "context": context, "plan": "", "project_context": project_context, "file_context": file_context},
        context=run_context,
        trigger_source="advanced.multi_agent",
        progress_callback=progress_callback,
        cancel_check=cancel_check,
        run_created_callback=run_created_callback,
        permission_mode=permission_mode,
    )

    # A Workflow UI request pauses durable execution. Keep the original caller
    # alive while the request tray resolves and resumes that same run; there is
    # no product deadline. This also handles several sequential requests.
    waiting_statuses = {
        "running", "paused", "needs_input", "needs_secret",
        "needs_elevation", "waiting_approval", "needs_reconciliation",
    }
    while str(run.get("status") or "") in waiting_statuses:
        if cancel_check and cancel_check():
            if str(run.get("status") or "") not in {"completed", "failed", "cancelled"}:
                try:
                    run = cancel_workflow_run(
                        str(run.get("run_id") or ""),
                        db_path=get_workflow_db_path(),
                    )
                except ValueError:
                    pass
            break
        time.sleep(0.2)
        current = _app_get_workflow_run(
            db_path=get_workflow_db_path(),
            run_id=str(run.get("run_id") or ""),
        )
        if current is None:
            break
        run = current

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

    template = _app_get_workflow_template(db_path=get_workflow_db_path(), workflow_id=workflow_id) or {"graph": {"steps": []}}
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
    from app.application.workflows.runtime import start_workflow_run

    seed_builtin_workflows()
    run = start_workflow_run(
        workflow_id=MULTI_AGENT_FULL_WORKFLOW_ID,
        workflow_input={"query": task, "context": "", "project_context": project_context, "file_context": file_context},
        context={"model_name": model_name, "memory_context": "", "num_ctx": num_ctx},
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
