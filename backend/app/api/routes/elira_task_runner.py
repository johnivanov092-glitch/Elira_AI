from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.data_files import data_file

router = APIRouter(prefix="/api/elira/task", tags=["elira-task-runner"])

DB_PATH = data_file("elira_state.db")


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
            "reason": "РўРµРєСѓС‰РёР№ С„Р°Р№Р» РІС‹Р±СЂР°РЅ РєР°Рє РіР»Р°РІРЅС‹Р№ РєР°РЅРґРёРґР°С‚ РЅР° РёР·РјРµРЅРµРЅРёРµ.",
        })

    for path in staged_paths[:10]:
        if path and path != current_path:
            items.append({
                "step": "inspect",
                "action": "modify",
                "path": path,
                "agent": "planner",
                "reason": "Р¤Р°Р№Р» СѓР¶Рµ staged Рё РІРєР»СЋС‡С‘РЅ РІ С‚РµРєСѓС‰СѓСЋ Р·Р°РґР°С‡Сѓ.",
            })

    goal_l = goal.lower()

    if any(word in goal_l for word in ["create", "СЃРѕР·РґР°Р№", "РґРѕР±Р°РІ", "РЅРѕРІС‹Р№ С„Р°Р№Р»", "component", "РєРѕРјРїРѕРЅРµРЅС‚"]):
        if not any(item["path"] == "frontend/src/components/NewTaskPanel.jsx" for item in items):
            items.append({
                "step": "create",
                "action": "create",
                "path": "frontend/src/components/NewTaskPanel.jsx",
                "agent": "coder",
                "reason": "РџРѕ С„РѕСЂРјСѓР»РёСЂРѕРІРєРµ Р·Р°РґР°С‡Рё РІРµСЂРѕСЏС‚РЅРѕ РЅСѓР¶РµРЅ РЅРѕРІС‹Р№ UI-РєРѕРјРїРѕРЅРµРЅС‚.",
            })

    if any(word in goal_l for word in ["api", "route", "router", "СЂРѕСѓС‚", "СЌРЅРґРїРѕРёРЅС‚", "backend"]):
        if not any(item["path"] == "backend/app/api/routes/new_task_route.py" for item in items):
            items.append({
                "step": "create",
                "action": "create",
                "path": "backend/app/api/routes/new_task_route.py",
                "agent": "coder",
                "reason": "Р—Р°РґР°С‡Р° РІС‹РіР»СЏРґРёС‚ РєР°Рє backend/API-РёР·РјРµРЅРµРЅРёРµ.",
            })

    if not items:
        items.append({
            "step": "inspect",
            "action": "inspect",
            "path": current_path or "project",
            "agent": "planner",
            "reason": "РЎРЅР°С‡Р°Р»Р° РЅСѓР¶РЅРѕ СѓС‚РѕС‡РЅРёС‚СЊ РѕР±Р»Р°СЃС‚СЊ РїСЂРѕРµРєС‚Р° Рё РІС‹Р±СЂР°С‚СЊ С„Р°Р№Р»С‹.",
        })

    return items


def build_supervisor_pipeline(mode: str) -> List[dict]:
    return [
        {"agent": "planner", "status": "done", "description": f"РџРѕСЃС‚СЂРѕРёР» РїР»Р°РЅ РґР»СЏ СЂРµР¶РёРјР° {mode}."},
        {"agent": "coder", "status": "ready", "description": "Р“РѕС‚РѕРІРёС‚ preview patch Рё staged changes."},
        {"agent": "reviewer", "status": "ready", "description": "РџСЂРѕРІРµСЂРёС‚ diff, history Рё verify."},
        {"agent": "tester", "status": "queued", "description": "Р—Р°РїСѓСЃС‚РёС‚ verify pipeline РїРѕСЃР»Рµ apply."},
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
            "РџСЂРѕРІРµСЂСЊ plan.",
            "РћС‚РєСЂРѕР№ РЅСѓР¶РЅС‹Рµ С„Р°Р№Р»С‹ Рё РїРѕРґРіРѕС‚РѕРІСЊ preview patch.",
            "РЎРґРµР»Р°Р№ apply РґР»СЏ РІС‹Р±СЂР°РЅРЅС‹С… С„Р°Р№Р»РѕРІ.",
            "Р—Р°РїСѓСЃС‚Рё verify.",
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

