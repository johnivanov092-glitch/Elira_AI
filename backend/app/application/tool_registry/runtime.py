"""Tool Registry — dynamic registry for Agent OS Phase 2."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.application.tool_registry.builtins import build_builtin_tools
from app.application.tool_registry import store as registry_store
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
    updated_at TEXT NOT NULL,
    permission TEXT NOT NULL DEFAULT 'auto',
    side_effect INTEGER NOT NULL DEFAULT 0,
    scopes TEXT NOT NULL DEFAULT '[]',
    timeout_seconds INTEGER NOT NULL DEFAULT 30,
    max_output_chars INTEGER NOT NULL DEFAULT 50000,
    idempotent INTEGER NOT NULL DEFAULT 0
);
"""

_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH)


def _init_db() -> None:
    registry_store.init_db(conn_factory=_conn, create_sql=_CREATE_SQL)
    registry_store.migrate_toolspec_columns(conn_factory=_conn)


_init_db()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return registry_store.row_to_dict(row)


def _noop_handler(args: dict[str, Any]) -> dict[str, Any]:
    return {"ok": False, "error": "No handler registered for this tool"}


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
    permission: str = "auto",
    side_effect: bool = False,
    scopes: list[str] | None = None,
    timeout_seconds: int = 30,
    max_output_chars: int = 50000,
    idempotent: bool = False,
) -> dict:
    return registry_store.register_tool(
        conn_factory=_conn,
        handlers=_handlers,
        now_func=_now,
        get_tool_func=get_tool,
        name=name,
        handler=handler,
        display_name=display_name,
        display_name_ru=display_name_ru,
        description=description,
        description_ru=description_ru,
        category=category,
        parameters_schema=parameters_schema,
        source=source,
        permission=permission,
        side_effect=side_effect,
        scopes=scopes,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
        idempotent=idempotent,
    )


def register_tool_from_dict(tool_def: dict, handler: Callable | None = None) -> dict:
    return registry_store.register_tool_from_dict(
        register_tool_func=register_tool,
        handlers=_handlers,
        noop_handler=_noop_handler,
        tool_def=tool_def,
        handler=handler,
    )


def get_tool(name: str) -> dict | None:
    return registry_store.get_tool(
        conn_factory=_conn,
        handlers=_handlers,
        row_to_dict_func=_row_to_dict,
        name=name,
    )


def list_tools_with_schemas(
    category: str | None = None,
    source: str | None = None,
    enabled_only: bool = True,
) -> list[dict]:
    return registry_store.list_tools_with_schemas(
        conn_factory=_conn,
        handlers=_handlers,
        row_to_dict_func=_row_to_dict,
        category=category,
        source=source,
        enabled_only=enabled_only,
    )


def update_tool(name: str, updates: dict) -> dict:
    return registry_store.update_tool(
        conn_factory=_conn,
        now_func=_now,
        get_tool_func=get_tool,
        name=name,
        updates=updates,
    )


def delete_tool(name: str) -> dict:
    return registry_store.delete_tool(
        conn_factory=_conn,
        handlers=_handlers,
        name=name,
    )


def _execute_raw(name: str, args: dict[str, Any] | None = None) -> dict:
    """Execute handler without event emission — caller owns the audit trail."""
    return registry_store.execute_tool(
        handlers=_handlers,
        get_tool_func=get_tool,
        name=name,
        args=args,
    )


def execute_tool(name: str, args: dict[str, Any] | None = None) -> dict:
    result = _execute_raw(name, args)
    try:
        from app.application.event_bus import runtime as _eb
        _eb.emit_event(
            event_type="tool.executed",
            payload={
                "tool_name": name,
                "args": args or {},
                "success": result.get("ok", True),
                "error": result.get("error"),
            },
        )
    except Exception:
        pass
    return result


def validate_tool_args(name: str, args: dict) -> list[str]:
    return registry_store.validate_tool_args(
        get_tool_func=get_tool,
        name=name,
        args=args,
    )


_BUILTIN_SEEDED = False


def seed_builtin_tools() -> int:
    global _BUILTIN_SEEDED
    if _BUILTIN_SEEDED:
        return 0

    created = 0
    for tool_def in build_builtin_tools():
        tool_copy = dict(tool_def)
        handler = tool_copy.pop("handler")
        register_tool(handler=handler, **tool_copy)
        created += 1

    _BUILTIN_SEEDED = True
    return created
