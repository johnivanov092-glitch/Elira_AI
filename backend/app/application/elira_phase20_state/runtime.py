"""Application-layer runtime for the Elira phase20 execution-state endpoints.

Owns the SQLite schema for ``phase20_execution_state``, the checkpoint/
rollback builders, and persistence.  The HTTP layer in
``api/routes/elira_phase20_state.py`` is a thin FastAPI shell.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import List

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
            CREATE TABLE IF NOT EXISTS phase20_execution_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                checkpoint_json TEXT NOT NULL,
                queue_json TEXT NOT NULL,
                rollback_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# ───────── Helpers ─────────

def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False)


# ───────── Builders ─────────

def build_checkpoints() -> list:
    return [
        {"step": "queue-built", "status": "done"},
        {"step": "preview-progress", "status": "ready"},
        {"step": "pre-apply-checkpoint", "status": "planned"},
        {"step": "post-apply-verify", "status": "planned"},
    ]


def build_rollback(staged_paths: List[str]) -> dict:
    return {
        "strategy": "checkpoint-based",
        "targets": staged_paths,
        "advice": [
            "Save patch history before apply.",
            "Keep staged set unchanged until verify.",
            "On conflict use rollback by file from history.",
        ],
    }


# ───────── Persistence ─────────

def persist_state(
    goal: str,
    checkpoints: list,
    queue_items: list,
    rollback: dict,
) -> int:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        cur = conn.execute(
            """
            INSERT INTO phase20_execution_state (
                goal, checkpoint_json, queue_json, rollback_json, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                goal,
                dumps(checkpoints),
                dumps(queue_items),
                dumps(rollback),
                "ready",
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_states(limit: int = 30) -> dict:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
    try:
        rows = conn.execute(
            """
            SELECT id, goal, status, created_at
            FROM phase20_execution_state
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"items": [dict(row) for row in rows]}
    finally:
        conn.close()


# ───────── High-level handler (HTTP-free) ─────────

def prepare_execution_state(
    goal: str,
    queue_items: list,
    staged_paths: List[str],
) -> dict:
    """Build and persist an execution-state record, returning the response body."""
    checkpoints = build_checkpoints()
    rollback = build_rollback(staged_paths)

    state_id = persist_state(goal, checkpoints, queue_items, rollback)

    return {
        "status": "ok",
        "goal": goal,
        "checkpoints": checkpoints,
        "queue": queue_items,
        "rollback": rollback,
        "state_id": state_id,
        "created_at": datetime.utcnow().isoformat(),
    }
