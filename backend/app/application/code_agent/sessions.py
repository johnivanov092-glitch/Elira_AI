"""SQLite-backed storage for code-agent chat sessions.

Each session is a separate thread of conversation with its own history,
optional per-session model and project_root overrides, and a pin flag.

Database lives at data/code_agent_sessions.db. The whole session
(metadata + turns) is the unit of storage; the frontend keeps a
localStorage cache for fast initial render but treats this as the
source of truth.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

DB_PATH = sqlite_data_file("code_agent_sessions.db", key_tables=("sessions",))


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def _now_ms() -> int:
    return int(time.time() * 1000)


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                project_root TEXT,
                model TEXT,
                num_ctx INTEGER,
                pinned INTEGER NOT NULL DEFAULT 0,
                turns_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_pinned ON sessions(pinned DESC, updated_at DESC)")
        conn.commit()
    finally:
        conn.close()


init_db()


def _new_id() -> str:
    return f"s-{_now_ms()}-{uuid.uuid4().hex[:6]}"


def _row_to_meta(row: sqlite3.Row) -> dict[str, Any]:
    """Lightweight session metadata (no turns)."""
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "project_root": row["project_root"],
        "model": row["model"],
        "num_ctx": row["num_ctx"],
        "pinned": int(row["pinned"]) == 1,
    }


def list_sessions(query: str | None = None) -> list[dict[str, Any]]:
    """List sessions ordered by pinned first, then most recent.
    Optional `query` filters by title substring (case-insensitive)."""
    conn = _conn()
    try:
        if query and query.strip():
            rows = conn.execute(
                """
                SELECT id, title, created_at, updated_at, project_root, model, num_ctx, pinned
                FROM sessions
                WHERE LOWER(title) LIKE ?
                ORDER BY pinned DESC, updated_at DESC
                """,
                (f"%{query.strip().lower()}%",),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, title, created_at, updated_at, project_root, model, num_ctx, pinned
                FROM sessions
                ORDER BY pinned DESC, updated_at DESC
                """
            ).fetchall()
        return [_row_to_meta(r) for r in rows]
    finally:
        conn.close()


def get_session(session_id: str) -> dict[str, Any] | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        meta = _row_to_meta(row)
        try:
            meta["turns"] = json.loads(row["turns_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            meta["turns"] = []
        return meta
    finally:
        conn.close()


def create_session(
    title: str = "Новый чат",
    project_root: str | None = None,
    model: str | None = None,
    num_ctx: int | None = None,
) -> dict[str, Any]:
    session_id = _new_id()
    now = _now_ms()
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO sessions (id, title, created_at, updated_at, project_root, model, num_ctx, pinned, turns_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, '[]')
            """,
            (session_id, title or "Новый чат", now, now, project_root, model, num_ctx),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "id": session_id,
        "title": title or "Новый чат",
        "created_at": now,
        "updated_at": now,
        "project_root": project_root,
        "model": model,
        "num_ctx": num_ctx,
        "pinned": False,
        "turns": [],
    }


def update_session(session_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    """Update mutable fields. Allowed keys: title, project_root, model,
    num_ctx, pinned, turns. updated_at is bumped automatically.
    """
    allowed = {"title", "project_root", "model", "num_ctx", "pinned", "turns"}
    sets: list[str] = []
    values: list[Any] = []
    for key, value in patch.items():
        if key not in allowed:
            continue
        if key == "turns":
            sets.append("turns_json = ?")
            values.append(json.dumps(value, ensure_ascii=False))
        elif key == "pinned":
            sets.append("pinned = ?")
            values.append(1 if value else 0)
        elif key == "num_ctx":
            sets.append("num_ctx = ?")
            values.append(int(value) if value is not None else None)
        else:
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return get_session(session_id)
    sets.append("updated_at = ?")
    values.append(_now_ms())
    values.append(session_id)
    conn = _conn()
    try:
        conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()
    return get_session(session_id)


def delete_session(session_id: str) -> bool:
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
