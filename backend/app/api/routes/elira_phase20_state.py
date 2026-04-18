from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import List

from app.core.data_files import data_file
from app.infrastructure.db.connection import connect_sqlite

router = APIRouter(prefix="/api/elira/phase20", tags=["elira-phase20-state"])

DB_PATH = data_file("elira_state.db")

def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS phase20_execution_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                checkpoint_json TEXT NOT NULL,
                queue_json TEXT NOT NULL,
                rollback_json TEXT NOT NULL,
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

class Phase20StatePayload(BaseModel):
    goal: str = Field(min_length=1)
    queue_items: List[dict] = []
    staged_paths: List[str] = []

@router.post("/execution-state")
def build_execution_state(payload: Phase20StatePayload):
    ensure_db()

    checkpoints = [
        {"step": "queue-built", "status": "done"},
        {"step": "preview-progress", "status": "ready"},
        {"step": "pre-apply-checkpoint", "status": "planned"},
        {"step": "post-apply-verify", "status": "planned"},
    ]

    rollback = {
        "strategy": "checkpoint-based",
        "targets": payload.staged_paths,
        "advice": [
            "РЎРѕС…СЂР°РЅРё patch history РїРµСЂРµРґ apply.",
            "Р”РµСЂР¶Рё staged РЅР°Р±РѕСЂ РЅРµРёР·РјРµРЅРЅС‹Рј РґРѕ verify.",
            "РџСЂРё РєРѕРЅС„Р»РёРєС‚Рµ РёСЃРїРѕР»СЊР·СѓР№ rollback РїРѕ С„Р°Р№Р»Р°Рј РёР· history.",
        ],
    }

    state = {
        "status": "ok",
        "goal": payload.goal,
        "checkpoints": checkpoints,
        "queue": payload.queue_items,
        "rollback": rollback,
        "created_at": datetime.utcnow().isoformat(),
    }

    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        cur = conn.execute(
            '''
            INSERT INTO phase20_execution_state (
                goal, checkpoint_json, queue_json, rollback_json, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                payload.goal,
                dumps(checkpoints),
                dumps(payload.queue_items),
                dumps(rollback),
                "ready",
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        state["state_id"] = cur.lastrowid
    finally:
        conn.close()

    return state

@router.get("/execution-state/list")
def list_execution_states(limit: int = 30):
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
    try:
        rows = conn.execute(
            '''
            SELECT id, goal, status, created_at
            FROM phase20_execution_state
            ORDER BY id DESC
            LIMIT ?
            ''',
            (limit,),
        ).fetchall()
        return {"items": [dict(row) for row in rows]}
    finally:
        conn.close()

