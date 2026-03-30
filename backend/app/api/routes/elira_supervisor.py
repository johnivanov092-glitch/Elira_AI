from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.data_files import data_file

router = APIRouter(prefix="/api/elira/supervisor", tags=["elira-supervisor"])

DB_PATH = data_file("elira_state.db")
PROJECT_ROOT = Path(".").resolve()
BLOCKED_PARTS = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "target",
}


def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS supervisor_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                mode TEXT NOT NULL,
                current_path TEXT,
                status TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


class SupervisorRunPayload(BaseModel):
    goal: str = Field(min_length=1)
    mode: str = Field(default="code")
    current_path: Optional[str] = None
    staged_paths: List[str] = []
    auto_apply: bool = False


class SupervisorExecutePayload(BaseModel):
    goal: str = Field(min_length=1)
    current_path: str = Field(min_length=1)
    current_content: str = ""
    auto_apply: bool = False


def dumps_json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def loads_json(text: str):
    return json.loads(text) if text else None


def resolve_project_path(rel_path: str) -> Path:
    target = (PROJECT_ROOT / rel_path).resolve()
    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path is outside project root")
    if set(target.parts) & BLOCKED_PARTS:
        raise HTTPException(status_code=403, detail="Path points to blocked area")
    return target


def build_plan(goal: str, current_path: str | None, staged_paths: List[str]) -> List[dict]:
    plan: List[dict] = []

    if current_path:
        plan.append({
            "action": "modify",
            "path": current_path,
            "reason": "РўРµРєСѓС‰РёР№ С„Р°Р№Р» РІС‹Р±СЂР°РЅ РєР°Рє РѕСЃРЅРѕРІРЅРѕР№ РєР°РЅРґРёРґР°С‚.",
        })

    for path in staged_paths[:8]:
        if path and path != current_path:
            plan.append({
                "action": "modify",
                "path": path,
                "reason": "Р¤Р°Р№Р» staged Рё СѓС‡Р°СЃС‚РІСѓРµС‚ РІ С‚РµРєСѓС‰РµРј СЃС†РµРЅР°СЂРёРё.",
            })

    goal_l = goal.lower()
    if any(word in goal_l for word in ["create", "СЃРѕР·РґР°Р№", "РґРѕР±Р°РІ", "РєРѕРјРїРѕРЅРµРЅС‚", "component"]):
        plan.append({
            "action": "create",
            "path": "frontend/src/components/SupervisorGeneratedPanel.jsx",
            "reason": "Р—Р°РґР°С‡Р° РІС‹РіР»СЏРґРёС‚ РєР°Рє РґРѕР±Р°РІР»РµРЅРёРµ РЅРѕРІРѕР№ UI-С„СѓРЅРєС†РёРё.",
        })

    if any(word in goal_l for word in ["api", "backend", "СЂРѕСѓС‚", "route", "router", "СЌРЅРґРїРѕРёРЅС‚"]):
        plan.append({
            "action": "create",
            "path": "backend/app/api/routes/supervisor_generated_route.py",
            "reason": "Р—Р°РґР°С‡Р° Р·Р°С‚СЂР°РіРёРІР°РµС‚ backend API.",
        })

    if not plan:
        plan.append({
            "action": "inspect",
            "path": current_path or "project",
            "reason": "РќСѓР¶РЅРѕ СЃРЅР°С‡Р°Р»Р° СѓС‚РѕС‡РЅРёС‚СЊ РѕР±Р»Р°СЃС‚СЊ РёР·РјРµРЅРµРЅРёР№.",
        })

    return plan[:12]


def build_steps(plan: List[dict], status_overrides: dict | None = None) -> List[dict]:
    preview_targets = [item["path"] for item in plan if item["action"] in {"modify", "create"}]
    statuses = {
        "planner": "done",
        "coder": "ready",
        "reviewer": "ready",
        "tester": "queued",
    }
    if status_overrides:
        statuses.update(status_overrides)

    return [
        {
            "agent": "planner",
            "status": statuses["planner"],
            "title": "РџРѕСЃС‚СЂРѕРµРЅРёРµ РїР»Р°РЅР°",
            "details": f"РџРѕРґРіРѕС‚РѕРІР»РµРЅРѕ {len(plan)} item(s).",
        },
        {
            "agent": "coder",
            "status": statuses["coder"],
            "title": "РџРѕРґРіРѕС‚РѕРІРєР° preview",
            "details": f"Preview targets: {', '.join(preview_targets) if preview_targets else 'РЅРµС‚'}",
        },
        {
            "agent": "reviewer",
            "status": statuses["reviewer"],
            "title": "Review",
            "details": "Diff, history Рё verify flow РїРѕРґРіРѕС‚РѕРІР»РµРЅС‹.",
        },
        {
            "agent": "tester",
            "status": statuses["tester"],
            "title": "Verify",
            "details": "Verify СЃС†РµРЅР°СЂРёР№ РїРѕРґРіРѕС‚РѕРІР»РµРЅ.",
        },
    ]


def persist_run(goal: str, mode: str, current_path: str | None, status: str, plan: list, steps: list, summary: dict) -> int:
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """
            INSERT INTO supervisor_runs (
                goal, mode, current_path, status,
                plan_json, steps_json, summary_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                goal,
                mode,
                current_path,
                status,
                dumps_json(plan),
                dumps_json(steps),
                dumps_json(summary),
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@router.post("/run")
def run_supervisor(payload: SupervisorRunPayload):
    plan = build_plan(payload.goal, payload.current_path, payload.staged_paths)
    steps = build_steps(plan, {"coder": "done" if payload.auto_apply else "ready"})
    summary = {
        "preview_targets": [item["path"] for item in plan if item["action"] in {"modify", "create"}],
        "next_steps": [
            "РћС‚РєСЂРѕР№ С„Р°Р№Р»С‹ РёР· РїР»Р°РЅР°.",
            "РЎРґРµР»Р°Р№ Preview Patch.",
            "РџСЂРѕРІРµСЂСЊ Diff Рё History.",
            "РЎРґРµР»Р°Р№ Apply Рё Verify.",
        ],
        "auto_apply": payload.auto_apply,
    }
    run_id = persist_run(
        payload.goal,
        payload.mode,
        payload.current_path,
        "planned",
        plan,
        steps,
        summary,
    )
    return {
        "status": "ok",
        "run_id": run_id,
        "goal": payload.goal,
        "mode": payload.mode,
        "current_path": payload.current_path,
        "plan": plan,
        "steps": steps,
        "summary": summary,
        "created_at": datetime.utcnow().isoformat(),
    }


@router.post("/execute")
def execute_supervisor(payload: SupervisorExecutePayload):
    target = resolve_project_path(payload.current_path)
    if target.is_dir():
      raise HTTPException(status_code=400, detail="Path points to directory")
    if not target.exists():
      raise HTTPException(status_code=404, detail="Target file not found")

    disk_content = target.read_text(encoding="utf-8")
    plan = build_plan(payload.goal, payload.current_path, [])
    proposed_content = payload.current_content or disk_content

    changed_vs_disk = proposed_content != disk_content
    diff_stats = {
        "added": max(0, proposed_content.count("\n") - disk_content.count("\n")),
        "removed": max(0, disk_content.count("\n") - proposed_content.count("\n")),
    }

    statuses = {
        "planner": "done",
        "coder": "done",
        "reviewer": "done",
        "tester": "done" if payload.auto_apply else "ready",
    }
    steps = build_steps(plan, statuses)

    summary = {
        "preview_targets": [payload.current_path],
        "next_steps": [
            "РџСЂРѕРІРµСЂСЊ preview content.",
            "РЎРґРµР»Р°Р№ Apply Patch РІ Code Workspace.",
            "Р—Р°РїСѓСЃС‚Рё Verify.",
        ] if not payload.auto_apply else [
            "Preview СЂР°СЃСЃС‡РёС‚Р°РЅ.",
            "РџРѕРґС‚РІРµСЂРґРё Apply Patch.",
            "РЎСЂР°Р·Сѓ РїРѕСЃР»Рµ apply РІС‹РїРѕР»РЅРё Verify.",
        ],
        "auto_apply": payload.auto_apply,
        "changed_vs_disk": changed_vs_disk,
        "diff_stats": diff_stats,
    }

    result = {
        "status": "ok",
        "goal": payload.goal,
        "mode": "code",
        "current_path": payload.current_path,
        "plan": plan,
        "steps": steps,
        "summary": summary,
        "preview": {
            "path": payload.current_path,
            "current_content": disk_content,
            "proposed_content": proposed_content,
            "changed_vs_disk": changed_vs_disk,
        },
        "verify": {
            "path": payload.current_path,
            "checks": [
                "Р¤Р°Р№Р» СЃСѓС‰РµСЃС‚РІСѓРµС‚",
                "Р¤Р°Р№Р» С‡РёС‚Р°РµС‚СЃСЏ РєР°Рє UTF-8",
                "Preview СЂР°СЃСЃС‡РёС‚Р°РЅ РґР»СЏ С‚РµРєСѓС‰РµРіРѕ С„Р°Р№Р»Р°",
                "Р“РѕС‚РѕРІ Рє Verify РїРѕСЃР»Рµ Apply",
            ],
        },
        "created_at": datetime.utcnow().isoformat(),
    }

    run_id = persist_run(
        payload.goal,
        "code",
        payload.current_path,
        "executed-preview",
        plan,
        steps,
        result,
    )
    result["run_id"] = run_id
    return result


@router.get("/history/list")
def list_supervisor_history(limit: int = 30):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, goal, mode, current_path, status, created_at
            FROM supervisor_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"items": [dict(row) for row in rows]}
    finally:
        conn.close()


@router.get("/history/get")
def get_supervisor_history(id: int):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, goal, mode, current_path, status,
                   plan_json, steps_json, summary_json, created_at
            FROM supervisor_runs
            WHERE id = ?
            """,
            (id,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        data = dict(row)
        data["plan"] = loads_json(data.pop("plan_json"))
        data["steps"] = loads_json(data.pop("steps_json"))
        data["summary"] = loads_json(data.pop("summary_json"))
        return data
    finally:
        conn.close()

