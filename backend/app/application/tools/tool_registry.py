"""Tool Registry — динамический реестр инструментов (Agent OS Phase 2).

Extracted from services/tool_registry.py.  services/tool_registry.py is now a
thin re-export facade; all logic lives here.
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
    """Регистрирует все встроенные инструменты."""
    global _BUILTIN_SEEDED
    if _BUILTIN_SEEDED:
        return 0

    from app.application.tools.builtin_tools import get_builtin_tool_definitions

    BUILTIN_TOOLS = get_builtin_tool_definitions()

    created = 0
    for tool_def in BUILTIN_TOOLS:
        handler = tool_def.pop("handler")
        register_tool(handler=handler, **tool_def)
        created += 1

    _BUILTIN_SEEDED = True
    return created