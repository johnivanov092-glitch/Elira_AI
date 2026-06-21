"""Patch history persistence — SQLite storage for elira-patch operations."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

DB_PATH: Path = sqlite_data_file("elira_state.db")


def _connect() -> sqlite3.Connection:
    # Centralised connect: parent mkdir, WAL journal, busy timeout, Row factory.
    return connect_sqlite(DB_PATH)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS patch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    action TEXT NOT NULL,
    before_content TEXT,
    after_content TEXT,
    diff_text TEXT,
    created_at TEXT NOT NULL
)
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.execute(_CREATE_SQL)
        conn.commit()
    finally:
        conn.close()


init_db()


def write_history(path: str, action: str, before_content: str, after_content: str, diff_text: str) -> int:
    now = datetime.utcnow().isoformat()
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO patch_history (path, action, before_content, after_content, diff_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (path, action, before_content, after_content, diff_text, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_history(path: str = "", limit: int = 50) -> list[dict]:
    conn = _connect()
    try:
        if path.strip():
            rows = conn.execute(
                "SELECT id, path, action, created_at FROM patch_history WHERE path = ? ORDER BY id DESC LIMIT ?",
                (path, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, path, action, created_at FROM patch_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_history_item(item_id: int) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, path, action, before_content, after_content, diff_text, created_at FROM patch_history WHERE id = ?",
            (item_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
