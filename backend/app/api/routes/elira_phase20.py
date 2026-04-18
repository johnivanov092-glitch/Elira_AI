from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.data_files import data_file
from app.infrastructure.db.connection import connect_sqlite

router = APIRouter(prefix="/api/elira/phase20", tags=["elira-phase20"])

DB_PATH = data_file("elira_state.db")
PROJECT_ROOT = Path(".").resolve()
BLOCKED_PARTS = {
    ".git", "node_modules", ".venv", "__pycache__", "dist", "build", "target"
}
ALLOWED_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".json", ".md", ".txt", ".html", ".rs"}


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


def build_reasoning(goal: str, selected_paths: List[str], project_files: List[str]) -> dict:
    goal_l = goal.lower()
    scope = "multi-file"
    if any(word in goal_l for word in ["api", "route", "backend", "СЌРЅРґРїРѕРёРЅС‚", "СЂРѕСѓС‚"]):
        scope = "backend"
    elif any(word in goal_l for word in ["ui", "button", "component", "panel", "РёРЅС‚РµСЂС„РµР№СЃ", "РєРЅРѕРїРє"]):
        scope = "ui"

    return {
        "scope": scope,
        "goal_summary": goal[:280],
        "selected_paths": selected_paths,
        "project_context_sample": project_files[:30],
        "advice": [
            "РСЃРїРѕР»СЊР·СѓР№ staged С„Р°Р№Р»С‹ РєР°Рє СЂР°Р±РѕС‡РёР№ РЅР°Р±РѕСЂ.",
            "РЎРЅР°С‡Р°Р»Р° preview, Р·Р°С‚РµРј apply, Р·Р°С‚РµРј verify.",
            "Р”Р»СЏ create/rename/delete РїСЂРѕРІРµСЂСЏР№ Project Map Рё РёСЃС‚РѕСЂРёСЋ РїР°С‚С‡РµР№.",
        ],
    }


def build_planner(goal: str, selected_paths: List[str], project_files: List[str]) -> dict:
    plan = []

    for path in selected_paths[:10]:
        plan.append({
            "action": "modify",
            "path": path,
            "reason": "Р¤Р°Р№Р» РІС‹Р±СЂР°РЅ РїРѕР»СЊР·РѕРІР°С‚РµР»РµРј Рё РІРєР»СЋС‡С‘РЅ РІ СЂР°Р±РѕС‡РёР№ РЅР°Р±РѕСЂ.",
        })

    goal_l = goal.lower()

    if any(word in goal_l for word in ["create", "СЃРѕР·РґР°Р№", "РЅРѕРІС‹Р№ С„Р°Р№Р»", "component", "РєРѕРјРїРѕРЅРµРЅС‚"]):
        suggested = "frontend/src/components/Phase20GeneratedPanel.jsx"
        if suggested not in selected_paths:
            plan.append({
                "action": "create",
                "path": suggested,
                "reason": "Р—Р°РґР°С‡Р° РІС‹РіР»СЏРґРёС‚ РєР°Рє РґРѕР±Р°РІР»РµРЅРёРµ РЅРѕРІРѕРіРѕ UI-РєРѕРјРїРѕРЅРµРЅС‚Р°.",
            })

    if any(word in goal_l for word in ["api", "route", "backend", "СЂРѕСѓС‚", "СЌРЅРґРїРѕРёРЅС‚"]):
        suggested = "backend/app/api/routes/phase20_generated_route.py"
        if suggested not in selected_paths:
            plan.append({
                "action": "create",
                "path": suggested,
                "reason": "Р—Р°РґР°С‡Р° Р·Р°С‚СЂР°РіРёРІР°РµС‚ backend API РёР»Рё СЂРѕСѓС‚РёРЅРі.",
            })

    if not plan and project_files:
        plan.append({
            "action": "inspect",
            "path": project_files[0],
            "reason": "РќРµС‚ РІС‹Р±СЂР°РЅРЅС‹С… С„Р°Р№Р»РѕРІ, РЅСѓР¶РµРЅ СЃС‚Р°СЂС‚РѕРІС‹Р№ inspect РїРѕ РїСЂРѕРµРєС‚Сѓ.",
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
        "history_targets": [item["path"] for item in planner.get("items", []) if item["action"] == "modify"],
        "notes": [
            "РџСЂРѕРІРµСЂСЊ unified diff РїРѕ РєР°Р¶РґРѕРјСѓ modify С„Р°Р№Р»Сѓ.",
            "РџСЂРѕРІРµСЂСЊ patch history РґР»СЏ РєРѕРЅС„Р»РёРєС‚СѓСЋС‰РёС… РёР·РјРµРЅРµРЅРёР№.",
            "РџРµСЂРµРґ batch apply СѓР±РµРґРёСЃСЊ, С‡С‚Рѕ staged РЅР°Р±РѕСЂ Р°РєС‚СѓР°Р»РµРЅ.",
        ],
    }


def build_tester(coder: dict) -> dict:
    targets = coder.get("preview_targets", [])
    return {
        "status": "ready",
        "verify_targets": targets,
        "checks": [
            "Batch verify recommended for staged files.",
            "РџРѕСЃР»Рµ apply РїСЂРѕРІРµСЂСЊ changed_vs_disk.",
            "РСЃС‚РѕСЂРёСЋ patch/task/supervisor runs Р¶РµР»Р°С‚РµР»СЊРЅРѕ СЃРѕС…СЂР°РЅРёС‚СЊ РїРѕСЃР»Рµ РІС‹РїРѕР»РЅРµРЅРёСЏ.",
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


def persist(goal: str, selected_paths: List[str], reasoning: dict, planner: dict, coder: dict, reviewer: dict, tester: dict, execution: dict) -> int:
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


class Phase20RunPayload(BaseModel):
    goal: str = Field(min_length=1)
    selected_paths: List[str] = []


@router.post("/run")
def run_phase20(payload: Phase20RunPayload):
    project_files = scan_project()
    reasoning = build_reasoning(payload.goal, payload.selected_paths, project_files)
    planner = build_planner(payload.goal, payload.selected_paths, project_files)
    coder = build_coder(planner)
    reviewer = build_reviewer(planner, coder)
    tester = build_tester(coder)
    execution = build_execution(planner, coder, reviewer, tester)

    result = {
        "status": "ok",
        "goal": payload.goal,
        "reasoning": reasoning,
        "planner": planner,
        "coder": coder,
        "reviewer": reviewer,
        "tester": tester,
        "execution": execution,
        "created_at": datetime.utcnow().isoformat(),
    }
    run_id = persist(payload.goal, payload.selected_paths, reasoning, planner, coder, reviewer, tester, execution)
    result["run_id"] = run_id
    return result


@router.get("/history/list")
def list_phase20_history(limit: int = 30):
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


@router.get("/history/get")
def get_phase20_history(id: int):
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
            (id,),
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

