from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/elira/task", tags=["elira-task-runner"])

DB_PATH = Path("data/elira_state.db")


def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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


class TaskRunPayload(BaseModel):
    goal: str = Field(min_length=1)
    mode: str = Field(default="code")
    current_path: Optional[str] = None
    staged_paths: List[str] = []


def dumps_json(data) -> str:
    import json
    return json.dumps(data, ensure_ascii=False)


def loads_json(text: str):
    import json
    if not text:
        return None
    return json.loads(text)


def build_plan(goal: str, current_path: str | None, staged_paths: List[str]) -> List[dict]:
    items: List[dict] = []

    if current_path:
        items.append({
            "step": "inspect",
            "action": "modify",
            "path": current_path,
            "agent": "planner",
            "reason": "Текущий файл выбран как главный кандидат на изменение.",
        })

    for path in staged_paths[:10]:
        if path and path != current_path:
            items.append({
                "step": "inspect",
                "action": "modify",
                "path": path,
                "agent": "planner",
                "reason": "Файл уже staged и включён в текущую задачу.",
            })

    goal_l = goal.lower()

    if any(word in goal_l for word in ["create", "создай", "добав", "новый файл", "component", "компонент"]):
        if not any(item["path"] == "frontend/src/components/NewTaskPanel.jsx" for item in items):
            items.append({
                "step": "create",
                "action": "create",
                "path": "frontend/src/components/NewTaskPanel.jsx",
                "agent": "coder",
                "reason": "По формулировке задачи вероятно нужен новый UI-компонент.",
            })

    if any(word in goal_l for word in ["api", "route", "router", "роут", "эндпоинт", "backend"]):
        if not any(item["path"] == "backend/app/api/routes/new_task_route.py" for item in items):
            items.append({
                "step": "create",
                "action": "create",
                "path": "backend/app/api/routes/new_task_route.py",
                "agent": "coder",
                "reason": "Задача выглядит как backend/API-изменение.",
            })

    if not items:
        items.append({
            "step": "inspect",
            "action": "inspect",
            "path": current_path or "project",
            "agent": "planner",
            "reason": "Сначала нужно уточнить область проекта и выбрать файлы.",
        })

    return items


def build_supervisor_pipeline(mode: str) -> List[dict]:
    return [
        {"agent": "planner", "status": "done", "description": f"Построил план для режима {mode}."},
        {"agent": "coder", "status": "ready", "description": "Готовит preview patch и staged changes."},
        {"agent": "reviewer", "status": "ready", "description": "Проверит diff, history и verify."},
        {"agent": "tester", "status": "queued", "description": "Запустит verify pipeline после apply."},
    ]


def persist_run(payload: TaskRunPayload, status: str, plan: list, logs: list, result: dict) -> int:
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
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
                payload.goal,
                payload.mode,
                payload.current_path,
                dumps_json(payload.staged_paths),
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


@router.post("/run")
def run_task(payload: TaskRunPayload):
    plan_items = build_plan(payload.goal, payload.current_path, payload.staged_paths)
    pipeline = build_supervisor_pipeline(payload.mode)
    started = datetime.utcnow().isoformat()

    logs = [
        "Task Runner started.",
        f"Mode: {payload.mode}",
        f"Goal: {payload.goal}",
        f"Planner built {len(plan_items)} item(s).",
        "Coder stage prepared preview targets.",
        "Reviewer stage is ready for diff and history checks.",
        "Tester stage is queued until apply/verify.",
    ]

    preview_targets = [item["path"] for item in plan_items if item["action"] in {"modify", "create"}]

    result = {
        "status": "ok",
        "mode": payload.mode,
        "goal": payload.goal,
        "started_at": started,
        "plan": plan_items,
        "pipeline": pipeline,
        "preview_targets": preview_targets,
        "logs": logs,
        "next_steps": [
            "Проверь plan.",
            "Открой нужные файлы и подготовь preview patch.",
            "Сделай apply для выбранных файлов.",
            "Запусти verify.",
        ],
    }
    run_id = persist_run(payload, "planned", plan_items, logs, result)
    result["run_id"] = run_id
    return result


@router.get("/history/list")
def list_task_history(limit: int = 30):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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


@router.get("/history/get")
def get_task_history(id: int):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, goal, mode, current_path, staged_paths_json, status,
                   plan_json, logs_json, result_json, created_at
            FROM task_runs
            WHERE id = ?
            """,
            (id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task run not found")

        data = dict(row)
        data["staged_paths"] = loads_json(data.pop("staged_paths_json"))
        data["plan"] = loads_json(data.pop("plan_json"))
        data["logs"] = loads_json(data.pop("logs_json"))
        data["result"] = loads_json(data.pop("result_json"))
        return data
    finally:
        conn.close()
