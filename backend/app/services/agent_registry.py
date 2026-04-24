"""Agent Registry — persistent agent registry compatibility facade (Agent OS Phase 1)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.application.agent_registry import builtins as agent_builtins
from app.application.agent_registry import store as registry_store
from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

DB_PATH: Path = sqlite_data_file("agent_registry.db")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_ru TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    description_ru TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'general',
    system_prompt TEXT NOT NULL DEFAULT '',
    model_preference TEXT NOT NULL DEFAULT '',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id TEXT PRIMARY KEY REFERENCES agents(id),
    state_json TEXT NOT NULL DEFAULT '{}',
    last_active_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    input_summary TEXT NOT NULL DEFAULT '',
    output_summary TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 0,
    route TEXT NOT NULL DEFAULT '',
    model_used TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON agent_runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_time ON agent_runs(started_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH)


def _init_db() -> None:
    registry_store.init_db(conn_factory=_conn, create_sql=_CREATE_SQL)


_init_db()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return registry_store.row_to_dict(row)


def register_agent(agent_def: dict) -> dict:
    return registry_store.register_agent(
        conn_factory=_conn,
        now_func=_now,
        get_agent_func=get_agent,
        update_agent_func=update_agent,
        agent_def=agent_def,
    )


def get_agent(agent_id: str) -> dict | None:
    return registry_store.get_agent(
        conn_factory=_conn,
        row_to_dict_func=_row_to_dict,
        agent_id=agent_id,
    )


def list_agents(role: str | None = None, enabled_only: bool = True, tag: str | None = None) -> list[dict]:
    return registry_store.list_agents(
        conn_factory=_conn,
        row_to_dict_func=_row_to_dict,
        role=role,
        enabled_only=enabled_only,
        tag=tag,
    )


def update_agent(agent_id: str, updates: dict) -> dict:
    return registry_store.update_agent(
        conn_factory=_conn,
        now_func=_now,
        get_agent_func=get_agent,
        agent_id=agent_id,
        updates=updates,
    )


def delete_agent(agent_id: str) -> dict:
    return registry_store.delete_agent(
        conn_factory=_conn,
        now_func=_now,
        agent_id=agent_id,
    )


def get_agent_state(agent_id: str) -> dict:
    return registry_store.get_agent_state(conn_factory=_conn, agent_id=agent_id)


def set_agent_state(agent_id: str, state: dict) -> dict:
    return registry_store.set_agent_state(
        conn_factory=_conn,
        now_func=_now,
        get_agent_state_func=get_agent_state,
        agent_id=agent_id,
        state=state,
    )


def record_agent_run(run_data: dict) -> dict:
    return registry_store.record_agent_run(
        conn_factory=_conn,
        now_func=_now,
        get_agent_state_func=get_agent_state,
        set_agent_state_func=set_agent_state,
        run_data=run_data,
    )


def get_agent_runs(agent_id: str, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    return registry_store.get_agent_runs(
        conn_factory=_conn,
        agent_id=agent_id,
        limit=limit,
        offset=offset,
    )


_BUILTIN_AGENTS_SEEDED = False


def seed_builtin_agents() -> int:
    global _BUILTIN_AGENTS_SEEDED
    if _BUILTIN_AGENTS_SEEDED:
        return 0

    created = 0
    for agent_def in agent_builtins.iter_builtin_agent_defs():
        if get_agent(agent_def["id"]):
            continue
        register_agent(agent_def)
        created += 1

    _BUILTIN_AGENTS_SEEDED = True
    return created


def resolve_agent(agent_id: str | None = None, role: str | None = None) -> dict | None:
    return registry_store.resolve_agent(
        get_agent_func=get_agent,
        list_agents_func=list_agents,
        agent_id=agent_id,
        role=role,
    )
