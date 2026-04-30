"""Application-layer runtime for the Elira execute + memory endpoints.

Owns the SQLite schema for ``memory_store``, the mode-reply builder, and
memory CRUD.  The HTTP layer in ``api/routes/elira_execute.py`` is a
thin FastAPI shell.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.data_files import data_file
from app.infrastructure.db.connection import connect_sqlite


DB_PATH = data_file("elira_state.db")


# ───────── DB ─────────

def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_store (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,
                title TEXT,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'chat',
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# ───────── Mode-reply builder ─────────

def build_mode_reply(
    content: str,
    mode: str,
    model: Optional[str],
    agent_profile: Optional[str],
) -> Dict[str, Any]:
    """Return the assistant response dict for the given mode."""
    mode = (mode or "chat").lower()
    content = content.strip()

    if mode == "code":
        assistant = (
            "Code mode activated.\n\n"
            "Next step: open a project file, build a diff preview, and prepare a patch plan.\n\n"
            f"Request: {content}"
        )
    elif mode == "research":
        assistant = (
            "Research mode activated.\n\n"
            "Next step: gather sources, extract key facts, and return a structured summary.\n\n"
            f"Request: {content}"
        )
    elif mode == "image":
        assistant = (
            "Text-to-Image mode activated.\n\n"
            "Next step: form an image prompt and generation parameters.\n\n"
            f"Request: {content}"
        )
    elif mode == "orchestrator":
        assistant = (
            "Orchestrator mode activated.\n\n"
            "Next step: split the task across sub-agents, build an execution plan, and track statuses.\n\n"
            f"Request: {content}"
        )
    else:
        assistant = (
            "Chat mode activated.\n\n"
            "Elira received the message and prepared a standard conversational reply.\n\n"
            f"Request: {content}"
        )

    return {
        "mode": mode,
        "assistant_content": assistant,
        "status": "ok",
        "model": model,
        "agent_profile": agent_profile,
    }


# ───────── Memory CRUD ─────────

def list_memory(q: str = "") -> dict:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
    try:
        if q.strip():
            rows = conn.execute(
                """
                SELECT id, chat_id, title, content, source, pinned, created_at, updated_at
                FROM memory_store
                WHERE content LIKE ? OR COALESCE(title, '') LIKE ?
                ORDER BY pinned DESC, updated_at DESC
                """,
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, chat_id, title, content, source, pinned, created_at, updated_at
                FROM memory_store
                ORDER BY pinned DESC, updated_at DESC
                """
            ).fetchall()

        items = [dict(row) for row in rows]
        for item in items:
            item["pinned"] = bool(item["pinned"])
        return {"items": items}
    finally:
        conn.close()


def save_memory(
    content: str,
    chat_id: Optional[str],
    title: Optional[str],
    source: str,
    pinned: bool,
) -> dict:
    ensure_db()
    now = datetime.utcnow().isoformat()
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        cur = conn.execute(
            """
            INSERT INTO memory_store (
                chat_id, title, content, source, pinned, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (chat_id, title, content, source, 1 if pinned else 0, now, now),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "chat_id": chat_id,
            "title": title,
            "content": content,
            "source": source,
            "pinned": pinned,
            "created_at": now,
            "updated_at": now,
        }
    finally:
        conn.close()


def delete_memory(memory_id: int) -> dict:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        conn.execute("DELETE FROM memory_store WHERE id = ?", (memory_id,))
        conn.commit()
        return {"status": "ok", "deleted_id": memory_id}
    finally:
        conn.close()
