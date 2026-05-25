from __future__ import annotations

import json
import sqlite3

from app.application.elira_memory.service import init_db as init_state_db
from app.core.data_files import sqlite_data_file
from app.core.persona_defaults import DEFAULT_PROFILE
from app.infrastructure.db.connection import connect_sqlite


DB_PATH = sqlite_data_file("elira_state.db", key_tables=("chats", "messages"))

DEFAULT_ROUTE_MAP = {
    "code": ["qwen2.5-coder:7b", "qwen3:8b", "gemma3:4b"],
    "project": ["qwen2.5-coder:7b", "qwen3:8b", "gemma3:4b"],
    "research": ["qwen3:8b", "mistral-nemo:latest", "gemma3:4b"],
    "chat": ["gemma3:4b", "qwen3:8b"],
    "code_agent": ["qwen2.5-coder:7b", "qwen3:8b"],
    "multi_agent": ["qwen3:8b", "qwen2.5-coder:7b"],
    "image": ["__skill_image_gen"],  # special: handled by image skill, not LLM model
}


def _ensure_planner_keywords_column():
    init_state_db()
    conn = _connect()
    try:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(settings)").fetchall()]
        if "planner_keywords" not in columns:
            conn.execute("ALTER TABLE settings ADD COLUMN planner_keywords TEXT DEFAULT '{}'")
            conn.commit()
    finally:
        conn.close()


def get_planner_keywords() -> dict[str, list[str]]:
    """User-overridden keyword bags. Empty dict ↦ planner uses defaults."""
    _ensure_planner_keywords_column()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT planner_keywords FROM settings WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    raw = row["planner_keywords"] or "{}"
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        return {str(k): [str(x) for x in v] for k, v in parsed.items() if isinstance(v, list)}
    except (json.JSONDecodeError, TypeError):
        return {}


def save_planner_keywords(bags: dict[str, list[str]]) -> dict[str, list[str]]:
    """Persist user-customised keyword bags. Pass {} to revert to defaults."""
    _ensure_planner_keywords_column()
    payload = json.dumps(bags or {}, ensure_ascii=False)
    conn = _connect()
    try:
        conn.execute(
            "UPDATE settings SET planner_keywords = ? WHERE id = 1",
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    return get_planner_keywords()


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
