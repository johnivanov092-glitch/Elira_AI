"""Application-layer runtime for the Elira phase20 multi-agent dev-loop route.

Owns the SQLite schema, JSON helpers, project scanner, reasoning/planner/coder/
reviewer/tester/execution builders, and persistence used by
``/api/elira/phase20/*``.  The HTTP layer in
``api/routes/elira_phase20.py`` is a thin FastAPI shell that defines the
router + the Pydantic request schema and delegates everything else here.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List

from app.core.data_files import data_file
from app.infrastructure.db.connection import connect_sqlite


DB_PATH = data_file("elira_state.db")
PROJECT_ROOT = Path(".").resolve()
BLOCKED_PARTS = {
    ".git", "node_modules", ".venv", "__pycache__", "dist", "build", "target"
}
ALLOWED_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".json", ".md", ".txt", ".html", ".rs"
}


# ───────── DB ─────────

def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS phase20_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                selected_paths_json TEXT NOT NULL,
                reasoning_json TEXT NOT NULL,
                planner_json TEXT NOT NULL,
                coder_json TEXT NOT NULL,
                reviewer_json TEXT NOT NULL,
                tester_json TEXT NOT NULL,
                execution_json TEXT NOT NULL,
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


def scan_project(limit: int = 600) -> List[str]:
    items: List[str] = []
    for path in PROJECT_ROOT.rglob("*"):
        if len(items) >= limit:
            break
        if not path.is_file():
            continue
        if set(path.parts) & BLOCKED_PARTS:
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        items.append(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"))
    return items


# ───────── Agent builders ─────────

def build_reasoning(goal: str, selected_paths: List[str], project_files: List[str]) -> dict:
    goal_l = goal.lower()
    scope = "multi-file"
    if any(word in goal_l for word in ["api", "route", "backend", "endpoint"]):
        scope = "backend"
    elif any(word in goal_l for word in ["ui", "button", "component", "panel"]):
        scope = "ui"

    return {
        "scope": scope,
        "goal_summary": goal[:280],
        "selected_paths": selected_paths,
        "project_context_sample": project_files[:30],
        "advice": [
            "Use staged files as the working set.",
            "Preview first, then apply, then verify.",
            "For create/rename/delete check Project Map and patch history.",
        ],
    }


def build_planner(goal: str, selected_paths: List[str], project_files: List[str]) -> dict:
    plan = []

    for path in selected_paths[:10]:
        plan.append({
            "action": "modify",
            "path": path,
            "reason": "File selected by user and included in working set.",
        })

    goal_l = goal.lower()

    if any(word in goal_l for word in ["create", "component"]):
        suggested = "frontend/src/components/Phase20GeneratedPanel.jsx"
        if suggested not in selected_paths:
            plan.append({
                "action": "create",
                "path": suggested,
                "reason": "Goal looks like adding a new UI component.",
            })

    if any(word in goal_l for word in ["api", "route", "backend", "endpoint"]):
        suggested = "backend/app/api/routes/phase20_generated_route.py"
        if suggested not in selected_paths:
            plan.append({
                "action": "create",
                "path": suggested,
                "reason": "Goal involves backend API or routing.",
            })

    if not plan and project_files:
        plan.append({
            "action": "inspect",
            "path": project_files[0],
            "reason": "No files selected; starting with project inspect.",
        })

    return {
        "status": "done",
        "items": plan[:14],
    }


def build_coder(planner: dict) -> dict:
    items = planner.get("items", [])
    ops = []
    preview_targets = []

    for item in items:
        action = item["action"]
        path = item["path"]
        if action == "modify":
            ops.append({
                "operation": "preview-edit",
                "path": path,
                "status": "ready",
            })
            preview_targets.append(path)
        elif action == "create":
            ops.append({
                "operation": "create-file",
                "path": path,
                "status": "planned",
            })
            preview_targets.append(path)
        else:
            ops.append({
                "operation": "inspect",
                "path": path,
                "status": "planned",
            })

    return {
        "status": "ready",
        "preview_targets": preview_targets,
        "operations": ops,
    }


def build_reviewer(planner: dict, coder: dict) -> dict:
    targets = coder.get("preview_targets", [])
    return {
        "status": "ready",
        "diff_targets": targets,
        "history_targets": [
            item["path"] for item in planner.get("items", []) if item["action"] == "modify"
        ],
        "notes": [
            "Check unified diff for each modify file.",
            "Check patch history for conflicting changes.",
            "Before batch apply ensure staged set is current.",
        ],
    }


def build_tester(coder: dict) -> dict:
    targets = coder.get("preview_targets", [])
    return {
        "status": "ready",
        "verify_targets": targets,
        "checks": [
            "Batch verify recommended for staged files.",
            "After apply check changed_vs_disk.",
            "Save patch/task/supervisor run history after execution.",
        ],
    }


def build_execution(planner: dict, coder: dict, reviewer: dict, tester: dict) -> dict:
    preview_targets = coder.get("preview_targets", [])
    return {
        "status": "ready",
        "flow": [
            "Planner -> plan",
            "Coder -> preview / create operations",
            "Reviewer -> diff / history review",
            "Tester -> verify targets",
            "Executor -> batch apply / batch verify",
        ],
        "preview_targets": preview_targets,
        "apply_recommended": bool(preview_targets),
        "verify_recommended": bool(preview_targets),
    }


# ───────── Persistence ─────────

def persist(
    goal: str,
    selected_paths: List[str],
    reasoning: dict,
    planner: dict,
    coder: dict,
    reviewer: dict,
    tester: dict,
    execution: dict,
) -> int:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        cur = conn.execute(
            """
            INSERT INTO phase20_runs (
                goal, selected_paths_json, reasoning_json, planner_json,
                coder_json, reviewer_json, tester_json, execution_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                goal,
                dumps(selected_paths),
                dumps(reasoning),
                dumps(planner),
                dumps(coder),
                dumps(reviewer),
                dumps(tester),
                dumps(execution),
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
            SELECT id, goal, created_at
            FROM phase20_runs
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
            SELECT id, goal, selected_paths_json, reasoning_json, planner_json,
                   coder_json, reviewer_json, tester_json, execution_json, created_at
            FROM phase20_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        data = dict(row)
        data["selected_paths"] = loads(data.pop("selected_paths_json"))
        data["reasoning"] = loads(data.pop("reasoning_json"))
        data["planner"] = loads(data.pop("planner_json"))
        data["coder"] = loads(data.pop("coder_json"))
        data["reviewer"] = loads(data.pop("reviewer_json"))
        data["tester"] = loads(data.pop("tester_json"))
        data["execution"] = loads(data.pop("execution_json"))
        return data
    finally:
        conn.close()


# ───────── High-level handler (HTTP-free) ─────────

def prepare_run(goal: str, selected_paths: List[str]) -> dict:
    """Build the phase20 multi-agent run payload, persist it, and return the response body.

    Mirrors the previous body of ``POST /api/elira/phase20/run``.
    """
    project_files = scan_project()
    reasoning = build_reasoning(goal, selected_paths, project_files)
    planner = build_planner(goal, selected_paths, project_files)
    coder = build_coder(planner)
    reviewer = build_reviewer(planner, coder)
    tester = build_tester(coder)
    execution = build_execution(planner, coder, reviewer, tester)

    result = {
        "status": "ok",
        "goal": goal,
        "reasoning": reasoning,
        "planner": planner,
        "coder": coder,
        "reviewer": reviewer,
        "tester": tester,
        "execution": execution,
        "created_at": datetime.utcnow().isoformat(),
    }
    run_id = persist(goal, selected_paths, reasoning, planner, coder, reviewer, tester, execution)
    result["run_id"] = run_id
    return result
