from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/jarvis/phase20", tags=["jarvis-phase20"])

DB_PATH = Path("data/jarvis_state.db")
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
    if any(word in goal_l for word in ["api", "route", "backend", "эндпоинт", "роут"]):
        scope = "backend"
    elif any(word in goal_l for word in ["ui", "button", "component", "panel", "интерфейс", "кнопк"]):
        scope = "ui"

    return {
        "scope": scope,
        "goal_summary": goal[:280],
        "selected_paths": selected_paths,
        "project_context_sample": project_files[:30],
        "advice": [
            "Используй staged файлы как рабочий набор.",
            "Сначала preview, затем apply, затем verify.",
            "Для create/rename/delete проверяй Project Map и историю патчей.",
        ],
    }


def build_planner(goal: str, selected_paths: List[str], project_files: List[str]) -> dict:
    plan = []

    for path in selected_paths[:10]:
        plan.append({
            "action": "modify",
            "path": path,
            "reason": "Файл выбран пользователем и включён в рабочий набор.",
        })

    goal_l = goal.lower()

    if any(word in goal_l for word in ["create", "создай", "новый файл", "component", "компонент"]):
        suggested = "frontend/src/components/Phase20GeneratedPanel.jsx"
        if suggested not in selected_paths:
            plan.append({
                "action": "create",
                "path": suggested,
                "reason": "Задача выглядит как добавление нового UI-компонента.",
            })

    if any(word in goal_l for word in ["api", "route", "backend", "роут", "эндпоинт"]):
        suggested = "backend/app/api/routes/phase20_generated_route.py"
        if suggested not in selected_paths:
            plan.append({
                "action": "create",
                "path": suggested,
                "reason": "Задача затрагивает backend API или роутинг.",
            })

    if not plan and project_files:
        plan.append({
            "action": "inspect",
            "path": project_files[0],
            "reason": "Нет выбранных файлов, нужен стартовый inspect по проекту.",
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
            "Проверь unified diff по каждому modify файлу.",
            "Проверь patch history для конфликтующих изменений.",
            "Перед batch apply убедись, что staged набор актуален.",
        ],
    }


def build_tester(coder: dict) -> dict:
    targets = coder.get("preview_targets", [])
    return {
        "status": "ready",
        "verify_targets": targets,
        "checks": [
            "Batch verify recommended for staged files.",
            "После apply проверь changed_vs_disk.",
            "Историю patch/task/supervisor runs желательно сохранить после выполнения.",
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
