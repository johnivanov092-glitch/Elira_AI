import json
import sqlite3

from app.core.data_files import sqlite_data_file
from app.core.persona_defaults import DEFAULT_PROFILE
from app.infrastructure.db.connection import connect_sqlite
from app.services.elira_memory_sqlite import init_db as init_state_db


DB_PATH = sqlite_data_file("elira_state.db", key_tables=("chats", "messages"))

DEFAULT_ROUTE_MAP = {
    "code": ["qwen2.5-coder:7b", "qwen3:8b", "gemma3:4b"],
    "project": ["qwen2.5-coder:7b", "qwen3:8b", "gemma3:4b"],
    "research": ["qwen3:8b", "mistral-nemo:latest", "gemma3:4b"],
    "chat": ["gemma3:4b", "qwen3:8b"],
}


def _connect():
    return connect_sqlite(
        DB_PATH,
        row_factory=sqlite3.Row,
        journal_mode=None,
    )


def _ensure_route_map_column():
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
    finally:
        conn.close()


def get_settings():
    _ensure_route_map_column()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT ollama_context, default_model, agent_profile, route_model_map
            FROM settings
            WHERE id = 1
            """
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {
            "ollama_context": 8192,
            "default_model": "gemma3:4b",
            "agent_profile": DEFAULT_PROFILE,
            "route_model_map": DEFAULT_ROUTE_MAP,
        }

    result = dict(row)
    try:
        result["route_model_map"] = json.loads(result.get("route_model_map") or "{}")
    except (json.JSONDecodeError, TypeError):
        result["route_model_map"] = dict(DEFAULT_ROUTE_MAP)

    for route, models in DEFAULT_ROUTE_MAP.items():
        result["route_model_map"].setdefault(route, models)
    return result


def save_settings(ollama_context, default_model, agent_profile, route_model_map=None):
    _ensure_route_map_column()
    payload = json.dumps(route_model_map if route_model_map else DEFAULT_ROUTE_MAP)
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE settings
            SET ollama_context = ?, default_model = ?, agent_profile = ?, route_model_map = ?
            WHERE id = 1
            """,
            (int(ollama_context), default_model, agent_profile, payload),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT ollama_context, default_model, agent_profile, route_model_map
            FROM settings
            WHERE id = 1
            """
        ).fetchone()
    finally:
        conn.close()

    result = dict(row)
    try:
        result["route_model_map"] = json.loads(result.get("route_model_map") or "{}")
    except (json.JSONDecodeError, TypeError):
        result["route_model_map"] = dict(DEFAULT_ROUTE_MAP)
    return result


def get_route_model_map() -> dict:
    settings = get_settings()
    return settings.get("route_model_map", DEFAULT_ROUTE_MAP)
