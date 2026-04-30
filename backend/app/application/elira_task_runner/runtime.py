"""Application-layer runtime for the Elira task-runner endpoints.

Owns the SQLite schema for ``task_runs``, JSON helpers, plan builder,
supervisor pipeline builder, and persistence.  The HTTP layer in
``api/routes/elira_task_runner.py`` is a thin FastAPI shell.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import List, Optional

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
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                mode TEXT NOT NULL,
                current_path TEXT,
                staged_paths_json TEXT,
                status TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                logs_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# ───────── Helpers ─────────

def dumps_json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def loads_json(text: str):
    if not text:
        return None
    return json.loads(text)


# ───────── Builders ─────────

def build_plan(goal: str, current_path: Optional[str], staged_paths: List[str]) -> List[dict]:
    items: List[dict] = []

    if current_path:
        items.append({
            "step": "inspect",
            "action": "modify",
            "path": current_path,
            "agent": "planner",
            "reason": "Current open file selected as primary modify candidate.",
        })

    for path in staged_paths[:10]:
        if path and path != current_path:
            items.append({
                "step": "inspect",
                "action": "modify",
                "path": path,
                "agent": "planner",
                "reason": "File already staged and included in current task.",
            })

    goal_l = goal.lower()

    if any(word in goal_l for word in ["create", "component"]):
        suggested = "frontend/src/components/NewTaskPanel.jsx"
        if not any(item["path"] == suggested for item in items):
            items.append({
                "step": "create",
                "action": "create",
                "path": suggested,
                "agent": "coder",
                "reason": "Goal looks like a new UI component is needed.",
            })

    if any(word in goal_l for word in ["api", "route", "router", "endpoint", "backend"]):
        suggested = "backend/app/api/routes/new_task_route.py"
        if not any(item["path"] == suggested for item in items):
            items.append({
                "step": "create",
                "action": "create",
                "path": suggested,
                "agent": "coder",
                "reason": "Goal looks like a backend/API change.",
            })

    if not items:
        items.append({
            "step": "inspect",
            "action": "inspect",
            "path": current_path or "project",
            "agent": "planner",
            "reason": "No files selected; needs project area inspection first.",
        })

    return items


def build_supervisor_pipeline(mode: str) -> List[dict]:
    return [
        {"agent": "planner", "status": "done", "description": f"Built plan for mode {mode}."},
        {"agent": "coder", "status": "ready", "description": "Prepares preview patch and staged changes."},
        {"agent": "reviewer", "status": "ready", "description": "Verifies diff, history, and verify targets."},
        {"agent": "tester", "status": "queued", "description": "Runs verify pipeline after apply."},
    ]


# ───────── Persistence ─────────

def persist_run(
    goal: str,
    mode: str,
    current_path: Optional[str],
    staged_paths: List[str],
    status: str,
    plan: list,
    logs: list,
    result: dict,
) -> int:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        cur = conn.execute(
            """
            INSERT INTO task_runs (
                goal, mode, current_path, staged_paths_json, status,
                plan_json, logs_json, result_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                goal,
                mode,
                current_path,
                dumps_json(staged_paths),
                status,
                dumps_json(plan),
                dumps_json(logs),
                dumps_json(result),
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
            SELECT id, goal, mode, current_path, status, created_at
            FROM task_runs
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
            SELECT id, goal, mode, current_path, staged_paths_json, status,
                   plan_json, logs_json, result_json, created_at
            FROM task_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        data = dict(row)
        data["staged_paths"] = loads_json(data.pop("staged_paths_json"))
        data["plan"] = loads_json(data.pop("plan_json"))
        data["logs"] = loads_json(data.pop("logs_json"))
        data["result"] = loads_json(data.pop("result_json"))
        return data
    finally:
        conn.close()


# ───────── High-level handler (HTTP-free) ─────────

def prepare_run(
    goal: str,
    mode: str,
    current_path: Optional[str],
    staged_paths: List[str],
) -> dict:
    """Build the task-runner payload, persist it, and return the response body."""
    plan_items = build_plan(goal, current_path, staged_paths)
    pipeline = build_supervisor_pipeline(mode)
    started = datetime.utcnow().isoformat()

    logs = [
        "Task Runner started.",
        f"Mode: {mode}",
        f"Goal: {goal}",
        f"Planner built {len(plan_items)} item(s).",
        "Coder stage prepared preview targets.",
        "Reviewer stage is ready for diff and history checks.",
        "Tester stage is queued until apply/verify.",
    ]

    preview_targets = [item["path"] for item in plan_items if item["action"] in {"modify", "create"}]

    result: dict = {
        "status": "ok",
        "mode": mode,
        "goal": goal,
        "started_at": started,
        "plan": plan_items,
        "pipeline": pipeline,
        "preview_targets": preview_targets,
        "logs": logs,
        "next_steps": [
            "Review plan.",
            "Open needed files and prepare preview patch.",
            "Apply for selected files.",
            "Run verify.",
        ],
    }
    run_id = persist_run(goal, mode, current_path, staged_paths, "planned", plan_items, logs, result)
    result["run_id"] = run_id
    return result
