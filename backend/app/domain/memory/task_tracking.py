"""
Task run and reflection tracking for agent memory.

Extracted from core/memory.py -- records of agent task executions
and reflection results for learning across sessions.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from app.core.config import DB_PATH


def _conn():
    return sqlite3.connect(str(DB_PATH))


def record_task_run(
    task_text: str,
    route_mode: str,
    graph_used: str,
    final_status: str,
    profile_name: str = "",
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO task_runs (profile_name, task_text, route_mode, graph_used, final_status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (profile_name or "")[:64],
                (task_text or "")[:4000],
                (route_mode or "")[:64],
                (graph_used or "")[:2000],
                (final_status or "")[:64],
            ),
        )
        conn.commit()


def record_reflection(
    task_text: str,
    answer_text: str,
    reflection: dict,
    profile_name: str = "",
):
    reflection = reflection or {}
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO reflections (
                profile_name, task_text, answer_text,
                answered, grounded, complete, actionable, safe, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (profile_name or "")[:64],
                (task_text or "")[:4000],
                (answer_text or "")[:12000],
                int(bool(reflection.get("answered", False))),
                int(bool(reflection.get("grounded", False))),
                int(bool(reflection.get("complete", False))),
                int(bool(reflection.get("actionable", False))),
                int(bool(reflection.get("safe", True))),
                str(reflection.get("notes", "") or "")[:4000],
            ),
        )
        conn.commit()


def get_recent_task_runs(profile_name: str = "", limit: int = 20):
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            rows = conn.execute(
                """
                SELECT id, task_text, route_mode, graph_used, final_status, created_at
                FROM task_runs
                WHERE profile_name = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (profile_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, task_text, route_mode, graph_used, final_status, created_at
                FROM task_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    return [
        {
            "id": r[0],
            "task_text": r[1],
            "route_mode": r[2],
            "graph_used": r[3],
            "final_status": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]


def get_recent_reflections(profile_name: str = "", limit: int = 20):
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            rows = conn.execute(
                """
                SELECT id, task_text, answered, grounded, complete, actionable, safe, notes, created_at
                FROM reflections
                WHERE profile_name = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (profile_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, task_text, answered, grounded, complete, actionable, safe, notes, created_at
                FROM reflections
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    return [
        {
            "id": r[0],
            "task_text": r[1],
            "answered": bool(r[2]),
            "grounded": bool(r[3]),
            "complete": bool(r[4]),
            "actionable": bool(r[5]),
            "safe": bool(r[6]),
            "notes": r[7],
            "created_at": r[8],
        }
        for r in rows
    ]
