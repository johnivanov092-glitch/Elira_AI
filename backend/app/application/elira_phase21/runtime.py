"""Application-layer runtime for the Elira phase21 autonomous-controller endpoints.

Owns the SQLite schema for ``phase21_runs``, the controller builder, and
persistence.  The HTTP layer in ``api/routes/elira_phase21.py`` is a
thin FastAPI shell.
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
            CREATE TABLE IF NOT EXISTS phase21_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                queue_json TEXT NOT NULL,
                execution_state_json TEXT NOT NULL,
                controller_json TEXT NOT NULL,
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


def loads(data: str):
    return json.loads(data) if data else None


# ───────── Builder ─────────

def build_controller(queue_items: list, execution_state: dict) -> dict:
    """Build the autonomous-controller descriptor from queue and execution state."""
    return {
        "mode": "autonomous-controller",
        "steps": [
            {"step": "load-queue", "status": "done"},
            {"step": "consume-preview-queue", "status": "ready" if queue_items else "skip"},
            {"step": "checkpoint-before-apply", "status": "ready" if execution_state else "planned"},
            {"step": "batch-apply-controller", "status": "ready" if queue_items else "planned"},
            {"step": "batch-verify-controller", "status": "ready" if queue_items else "planned"},
            {"step": "rollback-fallback", "status": "ready"},
        ],
        "summary": {
            "queue_count": len(queue_items),
            "has_execution_state": bool(execution_state),
            "apply_allowed": bool(queue_items),
            "verify_allowed": bool(queue_items),
        },
        "notes": [
            "Controller uses queue and execution state as input.",
            "Preview queue completes first, then apply, then verify.",
            "On error the rollback strategy from execution state is used.",
        ],
    }


# ───────── Persistence ─────────

def persist(
    goal: str,
    queue_items: list,
    execution_state: dict,
    controller: dict,
) -> int:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        cur = conn.execute(
            """
            INSERT INTO phase21_runs (
                goal, queue_json, execution_state_json, controller_json, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                goal,
                dumps(queue_items),
                dumps(execution_state),
                dumps(controller),
                "ready",
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_runs(limit: int = 30) -> dict:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
    try:
        rows = conn.execute(
            """
            SELECT id, goal, status, created_at
            FROM phase21_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"items": [dict(row) for row in rows]}
    finally:
        conn.close()


def get_run(run_id: int) -> dict:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
    try:
        row = conn.execute(
            """
            SELECT id, goal, queue_json, execution_state_json, controller_json, status, created_at
            FROM phase21_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        data = dict(row)
        data["queue_items"] = loads(data.pop("queue_json"))
        data["execution_state"] = loads(data.pop("execution_state_json"))
        data["controller"] = loads(data.pop("controller_json"))
        return data
    finally:
        conn.close()


# ───────── High-level handler (HTTP-free) ─────────

def prepare_run(goal: str, queue_items: list, execution_state: dict) -> dict:
    """Build the phase21 controller payload, persist it, and return the response body."""
    controller = build_controller(queue_items, execution_state)
    run_id = persist(goal, queue_items, execution_state, controller)

    return {
        "status": "ok",
        "goal": goal,
        "queue_items": queue_items,
        "execution_state": execution_state,
        "controller": controller,
        "run_id": run_id,
        "created_at": datetime.utcnow().isoformat(),
    }
