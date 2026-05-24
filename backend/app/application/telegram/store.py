from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from app.core.config import DATA_DIR
from app.infrastructure.db.connection import connect_sqlite


DB_PATH = DATA_DIR / "integrations.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def connect_telegram_db() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def init_telegram_db() -> None:
    conn = connect_telegram_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS telegram_config (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS telegram_users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                allowed INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS telegram_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                direction TEXT,
                text TEXT,
                created_at TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


init_telegram_db()


def get_config_value(key: str, default: str = "") -> str:
    conn = connect_telegram_db()
    try:
        row = conn.execute("SELECT value FROM telegram_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_config_value(key: str, value: str) -> None:
    conn = connect_telegram_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO telegram_config (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def update_telegram_config(data: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "bot_token",
        "model",
        "profile",
        "allowed_users",
        "max_message_length",
        "use_memory",
        "use_web_search",
        "welcome_message",
    }
    updated: list[str] = []
    for key, value in data.items():
        if key not in allowed_keys:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        set_config_value(key, str(value))
        updated.append(key)
    return {"ok": True, "updated": updated}


def register_user(
    chat_id: int,
    *,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
) -> None:
    conn = connect_telegram_db()
    try:
        existing = conn.execute(
            "SELECT chat_id FROM telegram_users WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if existing:
            return

        allowed_cfg = get_config_value("allowed_users", "all")
        auto_allow = 1 if allowed_cfg == "all" else 0
        conn.execute(
            "INSERT INTO telegram_users (chat_id, username, first_name, last_name, allowed, created_at) VALUES (?,?,?,?,?,?)",
            (
                chat_id,
                username,
                first_name,
                last_name,
                auto_allow,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def is_user_allowed(chat_id: int) -> bool:
    allowed_cfg = get_config_value("allowed_users", "all")
    if allowed_cfg == "all":
        return True

    conn = connect_telegram_db()
    try:
        row = conn.execute(
            "SELECT allowed FROM telegram_users WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        return bool(row and row["allowed"])
    finally:
        conn.close()


def list_telegram_users() -> dict[str, Any]:
    conn = connect_telegram_db()
    try:
        rows = conn.execute(
            "SELECT * FROM telegram_users ORDER BY created_at DESC"
        ).fetchall()
        return {"ok": True, "users": [dict(row) for row in rows], "count": len(rows)}
    finally:
        conn.close()


def toggle_user_access(chat_id: int, allowed: bool) -> dict[str, Any]:
    conn = connect_telegram_db()
    try:
        conn.execute(
            "UPDATE telegram_users SET allowed = ? WHERE chat_id = ?",
            (int(allowed), chat_id),
        )
        conn.commit()
        return {"ok": True, "chat_id": chat_id, "allowed": allowed}
    finally:
        conn.close()


def log_message(chat_id: int, direction: str, text: str) -> None:
    conn = connect_telegram_db()
    try:
        conn.execute(
            "INSERT INTO telegram_log (chat_id, direction, text, created_at) VALUES (?,?,?,?)",
            (chat_id, direction, text[:2000], datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_telegram_log(limit: int = 50) -> dict[str, Any]:
    conn = connect_telegram_db()
    try:
        rows = conn.execute(
            "SELECT * FROM telegram_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {"ok": True, "log": [dict(row) for row in rows], "count": len(rows)}
    finally:
        conn.close()
