from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.data_files import data_file

router = APIRouter(prefix="/api/elira/phase21", tags=["elira-phase21"])

DB_PATH = data_file("elira_state.db")

def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS phase21_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                queue_json TEXT NOT NULL,
                execution_state_json TEXT NOT NULL,
                controller_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.commit()
    finally:
        conn.close()

def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False)

def loads(data: str):
    return json.loads(data) if data else None

class Phase21RunPayload(BaseModel):
    goal: str = Field(min_length=1)
    queue_items: List[dict] = []
    execution_state: dict = {}

@router.post("/run")
def run_phase21(payload: Phase21RunPayload):
    ensure_db()

    queue_items = payload.queue_items or []
    execution_state = payload.execution_state or {}

    controller = {
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
            "РљРѕРЅС‚СЂРѕР»Р»РµСЂ РёСЃРїРѕР»СЊР·СѓРµС‚ queue Рё execution state РєР°Рє РІС…РѕРґ.",
            "РЎРЅР°С‡Р°Р»Р° Р·Р°РІРµСЂС€Р°РµС‚СЃСЏ preview queue, Р·Р°С‚РµРј apply, Р·Р°С‚РµРј verify.",
            "РџСЂРё РїСЂРѕР±Р»РµРјР°С… РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ rollback strategy РёР· execution state.",
        ],
    }

    result = {
        "status": "ok",
        "goal": payload.goal,
        "queue_items": queue_items,
        "execution_state": execution_state,
        "controller": controller,
        "created_at": datetime.utcnow().isoformat(),
    }

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            '''
            INSERT INTO phase21_runs (
                goal, queue_json, execution_state_json, controller_json, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                payload.goal,
                dumps(queue_items),
                dumps(execution_state),
                dumps(controller),
                "ready",
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        result["run_id"] = cur.lastrowid
    finally:
        conn.close()

    return result

@router.get("/history/list")
def list_phase21_history(limit: int = 30):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''
            SELECT id, goal, status, created_at
            FROM phase21_runs
            ORDER BY id DESC
            LIMIT ?
            ''',
            (limit,),
        ).fetchall()
        return {"items": [dict(row) for row in rows]}
    finally:
        conn.close()

@router.get("/history/get")
def get_phase21_history(id: int):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            '''
            SELECT id, goal, queue_json, execution_state_json, controller_json, status, created_at
            FROM phase21_runs
            WHERE id = ?
            ''',
            (id,),
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

