from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import List

router = APIRouter(prefix="/api/elira/phase20", tags=["elira-phase20-state"])

DB_PATH = Path("data/elira_state.db")

def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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
            "Сохрани patch history перед apply.",
            "Держи staged набор неизменным до verify.",
            "При конфликте используй rollback по файлам из history.",
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

    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
