"""Tool Registry — динамический реестр инструментов (Agent OS Phase 2).

Заменяет if/elif цепочку в tool_service.py динамическим реестром.
Метаданные (schema, description) в SQLite, хендлеры — in-memory dict.
БД: data/tool_registry.db
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

DB_PATH: Path = sqlite_data_file("tool_registry.db")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS tools (
    name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    display_name_ru TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    description_ru TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    parameters_schema_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'builtin',
    enabled INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# In-memory handler map: name -> callable(args) -> dict
_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH)


def _init_db() -> None:
    with _conn() as con:
        con.executescript(_CREATE_SQL)


_init_db()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "parameters_schema_json" in d:
        try:
            d["parameters_schema"] = json.loads(d["parameters_schema_json"])
        except (json.JSONDecodeError, TypeError):
            d["parameters_schema"] = {}
        del d["parameters_schema_json"]
    if "enabled" in d:
        d["enabled"] = bool(d["enabled"])
    return d


# ── Регистрация ──────────────────────────────────────────────

def register_tool(
    name: str,
    handler: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    display_name: str = "",
    display_name_ru: str = "",
    description: str = "",
    description_ru: str = "",
    category: str = "general",
    parameters_schema: dict[str, Any] | None = None,
    source: str = "builtin",
) -> dict:
    """Зарегистрировать инструмент: метаданные в SQLite, хендлер в памяти."""
    _handlers[name] = handler
    now = _now()

    with _conn() as con:
        con.execute(
            """INSERT INTO tools
               (name, display_name, display_name_ru, description, description_ru,
                category, parameters_schema_json, source, enabled, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                display_name=excluded.display_name,
                display_name_ru=excluded.display_name_ru,
                description=excluded.description,
                description_ru=excluded.description_ru,
                category=excluded.category,
                parameters_schema_json=excluded.parameters_schema_json,
                source=excluded.source,
                updated_at=excluded.updated_at""",
            (
                name, display_name, display_name_ru, description, description_ru,
                category, json.dumps(parameters_schema or {}, ensure_ascii=False),
                source, now, now,
            ),
        )
    return get_tool(name) or {"name": name}


def register_tool_from_dict(tool_def: dict, handler: Callable | None = None) -> dict:
    """Регистрация из dict (для API custom tools)."""
    name = tool_def["name"]
    if handler:
        _handlers[name] = handler
    return register_tool(
        name=name,
        handler=handler or _handlers.get(name, _noop_handler),
        display_name=tool_def.get("display_name", ""),
        display_name_ru=tool_def.get("display_name_ru", ""),
        description=tool_def.get("description", ""),
        description_ru=tool_def.get("description_ru", ""),
        category=tool_def.get("category", "custom"),
        parameters_schema=tool_def.get("parameters_schema"),
        source=tool_def.get("source", "custom"),
    )


def _noop_handler(args: dict) -> dict:
    return {"ok": False, "error": "No handler registered for this tool"}


# ── CRUD ─────────────────────────────────────────────────────

def get_tool(name: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM tools WHERE name = ?", (name,)).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    d["has_handler"] = name in _handlers
    return d


def list_tools_with_schemas(
    category: str | None = None,
    source: str | None = None,
    enabled_only: bool = True,
) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if enabled_only:
        clauses.append("enabled = 1")
    if category:
        clauses.append("category = ?")
        params.append(category)
    if source:
        clauses.append("source = ?")
        params.append(source)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _conn() as con:
        rows = con.execute(f"SELECT * FROM tools {where} ORDER BY category, name", params).fetchall()

    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["has_handler"] = d["name"] in _handlers
        result.append(d)
    return result


def update_tool(name: str, updates: dict) -> dict:
    allowed = {"display_name", "display_name_ru", "description", "description_ru", "category", "enabled"}
    sets: list[str] = []
    params: list = []

    for key, val in updates.items():
        if val is None:
            continue
        if key in allowed:
            if key == "enabled":
                val = 1 if val else 0
            sets.append(f"{key} = ?")
            params.append(val)
        elif key == "parameters_schema":
            sets.append("parameters_schema_json = ?")
            params.append(json.dumps(val, ensure_ascii=False))

    if not sets:
        return get_tool(name) or {}

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(name)

    with _conn() as con:
        con.execute(f"UPDATE tools SET {', '.join(sets)} WHERE name = ?", params)
    return get_tool(name) or {}


def delete_tool(name: str) -> dict:
    _handlers.pop(name, None)
    with _conn() as con:
        con.execute("DELETE FROM tools WHERE name = ?", (name,))
    return {"name": name, "deleted": True}


# ── Выполнение ───────────────────────────────────────────────

def execute_tool(name: str, args: dict[str, Any] | None = None) -> dict:
    """Вызвать зарегистрированный инструмент."""
    args = args or {}
    handler = _handlers.get(name)
    if not handler:
        return {"ok": False, "error": f"No handler for tool: {name}"}

    # Проверяем enabled
    tool_meta = get_tool(name)
    if tool_meta and not tool_meta.get("enabled", True):
        return {"ok": False, "error": f"Tool '{name}' is disabled"}

    try:
        result = handler(args)
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def validate_tool_args(name: str, args: dict) -> list[str]:
    """Базовая валидация аргументов по schema (required fields)."""
    tool = get_tool(name)
    if not tool:
        return [f"Tool '{name}' not found"]
    schema = tool.get("parameters_schema", {})
    required = schema.get("required", [])
    errors = []
    for field in required:
        if field not in args:
            errors.append(f"Missing required field: {field}")
    return errors


# ── Seed встроенных инструментов ─────────────────────────────

_BUILTIN_SEEDED = False


def seed_builtin_tools() -> int:
    """Регистрирует все встроенные инструменты из tool_service.py."""
    global _BUILTIN_SEEDED
    if _BUILTIN_SEEDED:
        return 0

    from app.services.tool_service import (
        search_memory_tool,
    )
    from app.services.web_service import search_web, research_web
    from app.services.python_runner import execute_python
    from app.services.project_service import (
        list_project_tree, read_project_file, write_project_file, search_project,
    )
    from app.services.project_patch_service import ProjectPatchService
    from app.services.library_service import list_library_files, build_library_context
    from app.services.git_service import git_status as _git_status_fn, git_commit as _git_commit_fn
    from app.services.project_map_service import ProjectMapService
    from app.services.project_brain_loop_service import ProjectBrainLoopService

    _patch = ProjectPatchService()
    _map_svc = ProjectMapService()
    _brain_svc = ProjectBrainLoopService()

    BUILTIN_TOOLS = [
        {
            "name": "search_memory",
            "handler": lambda a: search_memory_tool(str(a.get("profile", "default")), str(a.get("query", "")), int(a.get("limit", 5))),
            "display_name": "Search Memory", "display_name_ru": "Поиск в памяти",
            "category": "memory",
            "description": "Search semantic memory for relevant facts",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "profile": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
        },
        {
            "name": "search_web",
            "handler": lambda a: {"ok": True, "query": str(a.get("query", "")), "results": search_web(str(a.get("query", "")), max_results=int(a.get("max_results", 5)))},
            "display_name": "Search Web", "display_name_ru": "Поиск в интернете",
            "category": "web",
            "description": "Search the web for current information",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
        },
        {
            "name": "research_web",
            "handler": lambda a: {"ok": True, "query": str(a.get("query", "")), "results": (r := research_web(query=str(a.get("query", "")), max_results=int(a.get("max_results", 5)))), "count": len(r) if isinstance(r, list) else 0},
            "display_name": "Deep Research", "display_name_ru": "Глубокое исследование",
            "category": "web",
            "description": "Fetch and parse web pages for deep research",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
        },
        {
            "name": "browser_search",
            "handler": lambda a: __import__("app.services.browser_agent", fromlist=["BrowserAgent"]).BrowserAgent().search(str(a.get("query", "")), max_results=int(a.get("max_results", 5))),
            "display_name": "Browser Search", "display_name_ru": "Поиск через браузер",
            "category": "web",
            "description": "Search using headless browser",
        },
        {
            "name": "browser_run",
            "handler": lambda a: __import__("app.services.browser_agent", fromlist=["BrowserAgent"]).BrowserAgent().run(start_url=str(a.get("start_url", "")), steps=a.get("steps", []) if isinstance(a.get("steps", []), list) else [], headless=bool(a.get("headless", True))),
            "display_name": "Browser Run", "display_name_ru": "Запуск браузера",
            "category": "web",
            "description": "Run browser automation steps",
        },
        {
            "name": "multi_web_search",
            "handler": lambda a: __import__("app.services.web_multisearch_service", fromlist=["WebMultiSearchService"]).WebMultiSearchService().search(str(a.get("query", "")), max_results=int(a.get("max_results", 5))),
            "display_name": "Multi Web Search", "display_name_ru": "Мульти-поиск",
            "category": "web",
            "description": "Search across multiple web engines",
        },
        {
            "name": "python_execute",
            "handler": lambda a: execute_python(str(a.get("code", ""))),
            "display_name": "Python Execute", "display_name_ru": "Выполнить Python",
            "category": "code",
            "description": "Execute Python code in a sandboxed subprocess",
            "parameters_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
        },
        {
            "name": "list_project_tree",
            "handler": lambda a: list_project_tree(int(a.get("max_depth", 3)), int(a.get("max_items", 400))),
            "display_name": "Project Tree", "display_name_ru": "Дерево проекта",
            "category": "project",
            "description": "List project file tree",
        },
        {
            "name": "read_project_file",
            "handler": lambda a: read_project_file(str(a.get("path", "")), int(a.get("max_chars", 12000))),
            "display_name": "Read File", "display_name_ru": "Прочитать файл",
            "category": "project",
            "description": "Read a project file",
            "parameters_schema": {"type": "object", "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}}, "required": ["path"]},
        },
        {
            "name": "write_project_file",
            "handler": lambda a: write_project_file(str(a.get("path", "")), str(a.get("content", ""))),
            "display_name": "Write File", "display_name_ru": "Записать файл",
            "category": "project",
            "description": "Write content to a project file",
            "parameters_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        },
        {
            "name": "search_project",
            "handler": lambda a: search_project(str(a.get("query", "")), int(a.get("max_hits", 50))),
            "display_name": "Search Project", "display_name_ru": "Поиск в проекте",
            "category": "project",
            "description": "Search project files by content",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
        {
            "name": "preview_project_patch",
            "handler": lambda a: _patch.preview_patch(str(a.get("path", "")), str(a.get("new_content", "")), int(a.get("max_chars", 20000))),
            "display_name": "Preview Patch", "display_name_ru": "Предпросмотр патча",
            "category": "project",
            "description": "Preview file patch before applying",
        },
        {
            "name": "apply_project_patch",
            "handler": lambda a: _patch.apply_patch(str(a.get("path", "")), str(a.get("new_content", ""))),
            "display_name": "Apply Patch", "display_name_ru": "Применить патч",
            "category": "project",
            "description": "Apply a file patch",
        },
        {
            "name": "replace_in_file",
            "handler": lambda a: _patch.replace_in_file(str(a.get("path", "")), str(a.get("old_text", "")), str(a.get("new_text", "")), int(a.get("max_chars", 20000))),
            "display_name": "Replace in File", "display_name_ru": "Замена в файле",
            "category": "project",
            "description": "Replace text in a file",
        },
        {
            "name": "apply_replace_in_file",
            "handler": lambda a: _patch.apply_replace_in_file(str(a.get("path", "")), str(a.get("old_text", "")), str(a.get("new_text", "")), int(a.get("max_chars", 20000))),
            "display_name": "Apply Replace", "display_name_ru": "Применить замену",
            "category": "project",
            "description": "Apply text replacement in a file",
        },
        {
            "name": "rollback_project_patch",
            "handler": lambda a: _patch.rollback_patch(str(a.get("path", "")), str(a.get("backup_id", ""))),
            "display_name": "Rollback Patch", "display_name_ru": "Откатить патч",
            "category": "project",
            "description": "Rollback a file patch from backup",
        },
        {
            "name": "list_patch_backups",
            "handler": lambda a: _patch.list_backups(path=str(a.get("path", "")).strip() or None, limit=int(a.get("limit", 20))),
            "display_name": "List Backups", "display_name_ru": "Список бэкапов",
            "category": "project",
            "description": "List available patch backups",
        },
        {
            "name": "git_status",
            "handler": lambda a: _git_status_fn(),
            "display_name": "Git Status", "display_name_ru": "Статус Git",
            "category": "system",
            "description": "Show git repository status",
        },
        {
            "name": "git_commit_push",
            "handler": lambda a: _git_commit_fn(message=str(a.get("message", "AI update")), add_all=True),
            "display_name": "Git Commit", "display_name_ru": "Git коммит",
            "category": "system",
            "description": "Commit and push changes",
        },
        {
            "name": "list_library",
            "handler": lambda a: list_library_files(),
            "display_name": "List Library", "display_name_ru": "Библиотека",
            "category": "memory",
            "description": "List indexed library files",
        },
        {
            "name": "build_library_context",
            "handler": lambda a: build_library_context(),
            "display_name": "Library Context", "display_name_ru": "Контекст библиотеки",
            "category": "memory",
            "description": "Build context from indexed library",
        },
        {
            "name": "project_map_scan",
            "handler": lambda a: _map_svc.build_map(max_depth=int(a.get("max_depth", 4)), max_items=int(a.get("max_items", 500))),
            "display_name": "Project Map", "display_name_ru": "Карта проекта",
            "category": "project",
            "description": "Build project structure map",
        },
        {
            "name": "project_map_search",
            "handler": lambda a: _map_svc.search(str(a.get("query", "")), max_hits=int(a.get("max_hits", 30))),
            "display_name": "Map Search", "display_name_ru": "Поиск по карте",
            "category": "project",
            "description": "Search project map",
        },
        {
            "name": "project_brain_analyze",
            "handler": lambda a: _brain_svc.analyze(focus=str(a.get("focus", "backend")), max_iterations=int(a.get("max_iterations", 3))),
            "display_name": "Brain Analyze", "display_name_ru": "Анализ проекта",
            "category": "project",
            "description": "Deep analysis of project structure",
        },
        {
            "name": "project_brain_loop",
            "handler": lambda a: _brain_svc.run_loop(path=str(a.get("path", "")), new_content=str(a.get("new_content", "")), message=str(a.get("message", "AI Project Brain patch")), max_iterations=int(a.get("max_iterations", 1)), auto_push=bool(a.get("auto_push", False))),
            "display_name": "Brain Loop", "display_name_ru": "Петля разработки",
            "category": "project",
            "description": "Iterative project development loop",
        },
    ]

    created = 0
    for tool_def in BUILTIN_TOOLS:
        handler = tool_def.pop("handler")
        register_tool(handler=handler, **tool_def)
        created += 1

    _BUILTIN_SEEDED = True
    return created
