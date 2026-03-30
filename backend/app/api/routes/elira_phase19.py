from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.data_files import data_file

router = APIRouter(prefix="/api/elira/phase19", tags=["elira-phase19"])

DB_PATH = data_file("elira_state.db")
PROJECT_ROOT = Path(".").resolve()
BLOCKED_PARTS = {
    ".git", "node_modules", ".venv", "__pycache__", "dist", "build", "target"
}
ALLOWED_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".json", ".md", ".txt", ".html", ".rs"}


def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS phase19_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                mode TEXT NOT NULL,
                selected_paths_json TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                reasoning_json TEXT NOT NULL,
                files_json TEXT NOT NULL,
                verify_json TEXT NOT NULL,
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


def scan_project(limit: int = 400) -> List[str]:
    files: List[str] = []
    for path in PROJECT_ROOT.rglob("*"):
        if len(files) >= limit:
            break
        if not path.is_file():
            continue
        if set(path.parts) & BLOCKED_PARTS:
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        files.append(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"))
    return files


def build_project_reasoning(goal: str, selected_paths: List[str]) -> dict:
    goal_l = goal.lower()
    scope = "ui" if any(word in goal_l for word in ["ui", "button", "panel", "component", "РёРЅС‚РµСЂС„РµР№СЃ", "РєРЅРѕРїРє", "РїР°РЅРµР»"]) else "backend"
    if any(word in goal_l for word in ["api", "route", "backend", "endpoint", "СЂРѕСѓС‚", "СЌРЅРґРїРѕРёРЅС‚"]):
        scope = "backend"
    if any(word in goal_l for word in ["multi", "РЅРµСЃРєРѕР»СЊРєРѕ", "workflow", "pipeline", "loop"]):
        scope = "multi-file"

    return {
        "scope": scope,
        "goal_summary": goal[:240],
        "selected_paths": selected_paths,
        "advice": [
            "РЎРЅР°С‡Р°Р»Р° РїСЂРѕРІРµСЂСЊ Р·Р°С‚СЂРѕРЅСѓС‚С‹Рµ С„Р°Р№Р»С‹ С‡РµСЂРµР· preview diff.",
            "Р”Р»СЏ multi-file РёР·РјРµРЅРµРЅРёР№ Р»СѓС‡С€Рµ РїСЂРёРјРµРЅСЏС‚СЊ verify РїР°РєРµС‚РѕРј.",
            "РџРѕСЃР»Рµ apply СЃРѕС…СЂР°РЅСЏР№ РёСЃС‚РѕСЂРёСЋ patch/task/supervisor runs.",
        ],
    }


def build_multi_file_plan(goal: str, selected_paths: List[str], project_files: List[str]) -> List[dict]:
    plan: List[dict] = []

    for path in selected_paths[:8]:
        plan.append({
            "action": "modify",
            "path": path,
            "reason": "Р¤Р°Р№Р» РІС‹Р±СЂР°РЅ РїРѕР»СЊР·РѕРІР°С‚РµР»РµРј РґР»СЏ multi-file dev loop.",
        })

    goal_l = goal.lower()

    if any(word in goal_l for word in ["create", "СЃРѕР·РґР°Р№", "РЅРѕРІС‹Р№ С„Р°Р№Р»", "component", "РєРѕРјРїРѕРЅРµРЅС‚"]):
        suggested = "frontend/src/components/Phase19GeneratedPanel.jsx"
        if suggested not in selected_paths:
            plan.append({
                "action": "create",
                "path": suggested,
                "reason": "Р—Р°РґР°С‡Р° РїРѕС…РѕР¶Р° РЅР° СЃРѕР·РґР°РЅРёРµ РЅРѕРІРѕРіРѕ UI-РєРѕРјРїРѕРЅРµРЅС‚Р°.",
            })

    if any(word in goal_l for word in ["api", "route", "backend", "СЂРѕСѓС‚", "СЌРЅРґРїРѕРёРЅС‚"]):
        suggested = "backend/app/api/routes/phase19_generated_route.py"
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

    return plan[:12]


def build_file_operations(plan: List[dict]) -> List[dict]:
    ops = []
    for item in plan:
        action = item["action"]
        path = item["path"]
        if action == "modify":
            ops.append({
                "path": path,
                "operation": "preview-edit",
                "status": "ready",
            })
        elif action == "create":
            ops.append({
                "path": path,
                "operation": "create-file",
                "status": "planned",
            })
        else:
            ops.append({
                "path": path,
                "operation": "inspect",
                "status": "planned",
            })
    return ops


def build_verify_summary(plan: List[dict]) -> dict:
    preview_targets = [item["path"] for item in plan if item["action"] in {"modify", "create"}]
    return {
        "preview_targets": preview_targets,
        "verify_targets": preview_targets,
        "checks": [
            "Diff preview prepared",
            "Batch verify recommended for changed files",
            "History write recommended after apply",
        ],
    }


def persist(goal: str, mode: str, selected_paths: List[str], plan: list, reasoning: dict, files: list, verify: dict) -> int:
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """
            INSERT INTO phase19_runs (
                goal, mode, selected_paths_json, plan_json, reasoning_json, files_json, verify_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                goal,
                mode,
                dumps(selected_paths),
                dumps(plan),
                dumps(reasoning),
                dumps(files),
                dumps(verify),
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


class Phase19RunPayload(BaseModel):
    goal: str = Field(min_length=1)
    mode: str = Field(default="multi-file")
    selected_paths: List[str] = []


@router.post("/run")
def run_phase19(payload: Phase19RunPayload):
    project_files = scan_project()
    reasoning = build_project_reasoning(payload.goal, payload.selected_paths)
    plan = build_multi_file_plan(payload.goal, payload.selected_paths, project_files)
    file_ops = build_file_operations(plan)
    verify = build_verify_summary(plan)

    result = {
        "status": "ok",
        "mode": payload.mode,
        "goal": payload.goal,
        "reasoning": reasoning,
        "plan": plan,
        "file_operations": file_ops,
        "verify": verify,
        "project_sample": project_files[:80],
        "created_at": datetime.utcnow().isoformat(),
    }
    run_id = persist(payload.goal, payload.mode, payload.selected_paths, plan, reasoning, file_ops, verify)
    result["run_id"] = run_id
    return result


@router.get("/history/list")
def list_phase19_history(limit: int = 30):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, goal, mode, created_at
            FROM phase19_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"items": [dict(row) for row in rows]}
    finally:
        conn.close()


@router.get("/history/get")
def get_phase19_history(id: int):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, goal, mode, selected_paths_json, plan_json, reasoning_json, files_json, verify_json, created_at
            FROM phase19_runs
            WHERE id = ?
            """,
            (id,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        data = dict(row)
        data["selected_paths"] = loads(data.pop("selected_paths_json"))
        data["plan"] = loads(data.pop("plan_json"))
        data["reasoning"] = loads(data.pop("reasoning_json"))
        data["file_operations"] = loads(data.pop("files_json"))
        data["verify"] = loads(data.pop("verify_json"))
        return data
    finally:
        conn.close()

