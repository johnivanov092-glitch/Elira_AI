from __future__ import annotations

from typing import Any


def _build_native_code_agent_tools() -> list[dict[str, Any]]:
    """Metadata-only ToolSpec records for code_agent native tools.

    These records provide inventory/presentation metadata. Legacy permission,
    scope and timeout columns remain schema-compatible but are not execution
    gates. Handlers are noops — actual dispatch goes through BuiltinToolProvider,
    not through tool_registry.execute_tool.
    """
    def _noop(a: dict) -> dict:
        return {"ok": False, "error": "native tool — execute via code-agent, not tool_registry"}

    # ── Read-only (auto) ────────────────────────────────────────────────────
    auto_tools = [
        ("capability_load", "Load Capability", "system", "Expose one optional built-in tool group for the current run", 15, 10000, True),
        ("read_file",   "Read File",    "project", "Read a file in the project root",         15, 50000, True),
        ("glob",        "Glob",         "project", "List files matching a glob pattern",       15, 20000, True),
        ("grep",        "Grep",         "project", "Search files by content pattern",          15, 20000, True),
        ("project_map", "Project Map",  "project", "Structural overview: tree + entry points + signatures", 30, 30000, True),
        ("recall",      "Recall",       "project", "Search, index or report the project index", 60, 20000, True),
        ("library",     "Library",      "memory",  "Search and read the user's Library documents", 15, 20000, True),
        ("calc",        "Calculator",   "math",    "Exact arithmetic, percentages and algebra (no code execution)", 30, 20000, True),
        ("unit_convert", "Unit Convert", "math",   "Exact unit conversion (data, power, length, temperature...)", 15, 5000, True),
        ("resource_process", "Resource Inspect", "media", "Inspect metadata of an attached resource by resource_id on local CPU. Content processing uses materialize and mutable skills; no path.", 15, 20000, True),
    ]
    # ── Side-effect (require_approval) ─────────────────────────────────────
    approval_tools = [
        ("write_file",     "Write File",     "project", "Write content to a project file",       15,  5000, False),
        ("edit_file",      "Edit File",      "project", "Apply text replacement in a file",      15,  5000, False),
        ("run_bash",       "Run Bash",       "system",  "Execute a shell command in project",   120, 20000, False),
        ("run_server",     "Run Server",     "system",  "Start/manage a long-lived background server", 30, 20000, False),
        ("resource_materialize", "Materialize Resource", "media", "Copy a file attached to this run into the project workspace (new file, no overwrite) so file/run_bash tools can process it", 60, 5000, True),
        ("resource_publish", "Publish Resource", "media", "Validate and publish an already-produced project file as a downloadable artifact (streaming, hash-bound, no overwrite) via the existing download route", 120, 10000, True),
        ("mcp", "MCP", "system", "List, start, stop and configure MCP servers", 900, 50000, False),
    ]
    auto_side_effect_tools = [
        ("todo_update", "Todo Update", "task", "Read or update the durable run checklist", 15, 10000, False),
        ("delegate_task", "Delegate Task", "task", "Run a child agent until completion or Workflow Stop", 60, 50000, False),
        ("memory", "Memory", "memory", "Search, list, add or delete long-term facts about the user", 15, 10000, False),
    ]

    # Legacy inventory labels retained only for DB compatibility/observability.
    # ToolExecutor never interprets them as authorization scopes.
    _legacy_scope_labels = {
        "read_file": ["fs.read"], "glob": ["fs.read"], "grep": ["fs.read"],
        "project_map": ["fs.read"],
        "recall": ["fs.read"],
        "calc": [], "unit_convert": [],
        "todo_update": ["task.write"],
        "delegate_task": ["task.write", "fs.read"],
        "mcp": ["shell.exec", "net.outbound", "fs.read", "fs.write"],
        "memory": ["task.write"], "library": ["fs.read"],
        "write_file": ["fs.write"], "edit_file": ["fs.write"],
        "run_bash": ["shell.exec"], "run_server": ["shell.exec"],
        # Reads authorized resource metadata; content is materialized explicitly.
        "resource_process": ["fs.read"],
        # Reads the run-bound resource blob (fs.read) and writes a new workspace file (fs.write).
        "resource_materialize": ["fs.read", "fs.write"],
        # Reads a workspace file (fs.read) and writes a new download artifact (fs.write).
        "resource_publish": ["fs.read", "fs.write"],
        # Desktop control is shell-level power and is classified by Workflow impact.
    }

    result: list[dict[str, Any]] = []
    for name, display, cat, desc, timeout, max_chars, idempotent in auto_tools:
        result.append({
            "name": name, "handler": _noop,
            "display_name": display, "category": cat, "description": desc,
            "source": "code_agent",
            "permission": "auto", "side_effect": False,
            "scopes": _legacy_scope_labels.get(name, []),
            "idempotent": idempotent,
            "timeout_seconds": timeout, "max_output_chars": max_chars,
        })
    for name, display, cat, desc, timeout, max_chars, idempotent in auto_side_effect_tools:
        result.append({
            "name": name, "handler": _noop,
            "display_name": display, "category": cat, "description": desc,
            "source": "code_agent",
            "permission": "auto", "side_effect": True,
            "scopes": _legacy_scope_labels.get(name, []),
            "idempotent": idempotent,
            "timeout_seconds": timeout, "max_output_chars": max_chars,
        })
    for name, display, cat, desc, timeout, max_chars, idempotent in approval_tools:
        tool_def = {
            "name": name, "handler": _noop,
            "display_name": display, "category": cat, "description": desc,
            "source": "code_agent",
            "permission": "require_approval", "side_effect": True,
            "scopes": _legacy_scope_labels.get(name, []),
            "idempotent": idempotent,
            "timeout_seconds": timeout, "max_output_chars": max_chars,
        }
        result.append(tool_def)

    # Russian descriptions keep provider metadata readable in diagnostics.
    _ru_search_terms = {
        "resource_process": (
            "Метаданные прикреплённого файла / ресурса",
            "Посмотреть метаданные вложения по resource_id: inspect, имя, тип, размер, "
            "хеш. Чтение документов, OCR и расшифровка выполняются изменяемыми навыками "
            "после resource_materialize; resource_process не читает содержимое.",
        ),
        "resource_materialize": (
            "Материализовать вложение/ресурс в папку проекта",
            "Скопировать прикреплённый файл (ресурс, вложение) в рабочую папку проекта, "
            "чтобы обработать его обычными инструментами: materialize, положи файл в проект, "
            "сохрани вложение в проект, конвертировать, ffmpeg, распаковать архив, "
            "прогнать через python, resource, attachment, materialize resource, "
            "copy attachment into project, workspace",
        ),
        "resource_publish": (
            "Опубликовать готовый файл пользователю для скачивания",
            "Отдать/опубликовать готовый файл из проекта пользователю, сделать кнопку "
            "Скачать, дать ссылку на скачивание результата: publish, download, скачать, "
            "отдай файл, пришли файл, ссылка на скачивание, готовый файл, результат, "
            "artifact, deliver file, download link, workspace file",
        ),
    }
    for _spec in result:
        _ru = _ru_search_terms.get(_spec.get("name"))
        if _ru:
            _spec["display_name_ru"], _spec["description_ru"] = _ru
    return result






def build_builtin_tools() -> list[dict[str, Any]]:
    """Seed metadata for tools owned by the canonical provider registry."""
    return [
        *_build_native_code_agent_tools(),
    ]
