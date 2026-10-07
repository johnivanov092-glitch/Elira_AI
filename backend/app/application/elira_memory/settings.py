from __future__ import annotations

import json
import sqlite3

from app.application.elira_memory.service import init_db as init_state_db
from app.core.data_files import sqlite_data_file
from app.core.persona_defaults import DEFAULT_PROFILE
from app.infrastructure.db.connection import connect_sqlite


DB_PATH = sqlite_data_file("elira_state.db", key_tables=("chats", "messages"))

DEFAULT_ROUTE_MAP = {
    "code": ["local-model"],
    "project": ["local-model"],
    "research": ["local-model"],
    "chat": ["local-model"],
    "code_agent": ["local-model"],
    "image": ["__skill_image_gen"],  # special: handled by image skill, not LLM model
}


def _connect():
    return connect_sqlite(
        DB_PATH,
        row_factory=sqlite3.Row,
        journal_mode=None,
    )


def _ensure_settings_columns():
    init_state_db()
    conn = _connect()
    try:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(settings)").fetchall()]
        if "route_model_map" not in columns:
            conn.execute("ALTER TABLE settings ADD COLUMN route_model_map TEXT DEFAULT '{}'")
            conn.execute(
                "UPDATE settings SET route_model_map = ? WHERE id = 1",
                (json.dumps(DEFAULT_ROUTE_MAP),),
            )
            conn.commit()
        if "context_window" not in columns:
            conn.execute("ALTER TABLE settings ADD COLUMN context_window INTEGER NOT NULL DEFAULT 131072")
            conn.commit()
        if "orchestration_enabled" not in columns:
            conn.execute("ALTER TABLE settings ADD COLUMN orchestration_enabled INTEGER NOT NULL DEFAULT 0")
            conn.commit()
    finally:
        conn.close()


def get_settings():
    _ensure_settings_columns()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT context_window, default_model, agent_profile, route_model_map, orchestration_enabled
            FROM settings
            WHERE id = 1
            """
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {
            "context_window": 131072,
            "default_model": "local-model",
            "agent_profile": DEFAULT_PROFILE,
            "route_model_map": DEFAULT_ROUTE_MAP,
            "orchestration_enabled": False,
        }

    result = dict(row)
    result["orchestration_enabled"] = bool(result.get("orchestration_enabled"))
    try:
        result["route_model_map"] = json.loads(result.get("route_model_map") or "{}")
    except (json.JSONDecodeError, TypeError):
        result["route_model_map"] = dict(DEFAULT_ROUTE_MAP)

    for route, models in DEFAULT_ROUTE_MAP.items():
        result["route_model_map"].setdefault(route, models)
    return result


def save_settings(context_window, default_model, agent_profile, route_model_map=None, orchestration_enabled=False):
    _ensure_settings_columns()
    payload = json.dumps(route_model_map if route_model_map else DEFAULT_ROUTE_MAP)
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE settings
            SET context_window = ?, default_model = ?, agent_profile = ?, route_model_map = ?, orchestration_enabled = ?
            WHERE id = 1
            """,
            (int(context_window), default_model, agent_profile, payload, int(bool(orchestration_enabled))),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT context_window, default_model, agent_profile, route_model_map, orchestration_enabled
            FROM settings
            WHERE id = 1
            """
        ).fetchone()
    finally:
        conn.close()

    result = dict(row)
    result["orchestration_enabled"] = bool(result.get("orchestration_enabled"))
    try:
        result["route_model_map"] = json.loads(result.get("route_model_map") or "{}")
    except (json.JSONDecodeError, TypeError):
        result["route_model_map"] = dict(DEFAULT_ROUTE_MAP)
    return result


def get_route_model_map() -> dict:
    settings = get_settings()
    return settings.get("route_model_map", DEFAULT_ROUTE_MAP)
