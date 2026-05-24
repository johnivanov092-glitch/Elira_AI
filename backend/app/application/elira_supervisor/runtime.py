"""Application-layer runtime for the Elira supervisor route.

Owns SQLite bootstrap, JSON helpers, plan/step builders, run persistence,
history readers, and the FastAPI-free body of ``/api/elira/supervisor/*``
handlers. The HTTP layer in ``api/routes/elira_supervisor.py`` keeps the
Pydantic models, router, and HTTPException translation, but delegates all
non-trivial logic here.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple

from app.core.data_files import data_file
from app.infrastructure.db.connection import connect_sqlite


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


# ───────── DB ─────────

def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
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


# ───────── Helpers ─────────

def dumps_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def loads_json(text: str | None) -> Any:
    return json.loads(text) if text else None


def resolve_project_path(rel_path: str) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve ``rel_path`` against ``PROJECT_ROOT`` and validate it.

    Returns ``(path, None)`` on success or ``(None, error_kind)`` where
    ``error_kind`` is one of ``"outside_root"`` / ``"blocked"``. Callers
    are expected to translate the kind into an HTTP error.
    """
    target = (PROJECT_ROOT / rel_path).resolve()
    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        return None, "outside_root"
    if set(target.parts) & BLOCKED_PARTS:
        return None, "blocked"
    return target, None


# ───────── Plan / Steps ─────────

def build_plan(goal: str, current_path: str | None, staged_paths: List[str]) -> List[dict]:
    plan: List[dict] = []

    if current_path:
        plan.append({
            "action": "modify",
            "path": current_path,
            "reason": "Текущий файл выбран как основной кандидат.",
        })

    for path in staged_paths[:8]:
        if path and path != current_path:
            plan.append({
                "action": "modify",
                "path": path,
                "reason": "Файл staged и участвует в текущем сценарии.",
            })

    goal_l = goal.lower()
    if any(word in goal_l for word in ["create", "создай", "добав", "компонент", "component"]):
        plan.append({
            "action": "create",
            "path": "frontend/src/components/SupervisorGeneratedPanel.jsx",
            "reason": "Задача выглядит как добавление новой UI-функции.",
        })

    if any(word in goal_l for word in ["api", "backend", "роут", "route", "router", "эндпоинт"]):
        plan.append({
            "action": "create",
            "path": "backend/app/api/routes/supervisor_generated_route.py",
            "reason": "Задача затрагивает backend API.",
        })

    if not plan:
        plan.append({
            "action": "inspect",
            "path": current_path or "project",
            "reason": "Нужно сначала уточнить область изменений.",
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
            "title": "Построение плана",
            "details": f"Подготовлено {len(plan)} item(s).",
        },
        {
            "agent": "coder",
            "status": statuses["coder"],
            "title": "Подготовка preview",
            "details": f"Preview targets: {', '.join(preview_targets) if preview_targets else 'нет'}",
        },
        {
            "agent": "reviewer",
            "status": statuses["reviewer"],
            "title": "Review",
            "details": "Diff, history и verify flow подготовлены.",
        },
        {
            "agent": "tester",
            "status": statuses["tester"],
            "title": "Verify",
            "details": "Verify сценарий подготовлен.",
        },
    ]


# ───────── Persistence ─────────

def persist_run(
    goal: str,
    mode: str,
    current_path: str | None,
    status: str,
    plan: list,
    steps: list,
    summary: dict,
) -> int:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
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


def list_runs(limit: int = 30) -> dict:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
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


def get_run(run_id: int) -> dict:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
    try:
        row = conn.execute(
            """
            SELECT id, goal, mode, current_path, status,
                   plan_json, steps_json, summary_json, created_at
            FROM supervisor_runs
            WHERE id = ?
            """,
            (run_id,),
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


# ───────── High-level handlers (HTTP-free) ─────────

def prepare_run(
    goal: str,
    mode: str,
    current_path: str | None,
    staged_paths: List[str],
    auto_apply: bool,
) -> dict:
    """Build the supervisor run payload, persist it, and return the response body.

    Mirrors the previous body of ``POST /api/elira/supervisor/run`` and is the
    single source of truth for that handler shape.
    """
    plan = build_plan(goal, current_path, staged_paths)
    steps = build_steps(plan, {"coder": "done" if auto_apply else "ready"})
    summary = {
        "preview_targets": [item["path"] for item in plan if item["action"] in {"modify", "create"}],
        "next_steps": [
            "Открой файлы из плана.",
            "Сделай Preview Patch.",
            "Проверь Diff и History.",
            "Сделай Apply и Verify.",
        ],
        "auto_apply": auto_apply,
    }
    run_id = persist_run(
        goal,
        mode,
        current_path,
        "planned",
        plan,
        steps,
        summary,
    )
    return {
        "status": "ok",
        "run_id": run_id,
        "goal": goal,
        "mode": mode,
        "current_path": current_path,
        "plan": plan,
        "steps": steps,
        "summary": summary,
        "created_at": datetime.utcnow().isoformat(),
    }


def prepare_execute(
    goal: str,
    target: Path,
    current_path: str,
    current_content: str,
    auto_apply: bool,
) -> dict:
    """Build the supervisor execute payload (preview + verify) and persist it.

    ``target`` must already be the validated absolute path produced by
    ``resolve_project_path``; the route layer is responsible for that
    translation so this function stays HTTP-free.
    """
    disk_content = target.read_text(encoding="utf-8")
    plan = build_plan(goal, current_path, [])
    proposed_content = current_content or disk_content

    changed_vs_disk = proposed_content != disk_content
    diff_stats = {
        "added": max(0, proposed_content.count("\n") - disk_content.count("\n")),
        "removed": max(0, disk_content.count("\n") - proposed_content.count("\n")),
    }

    statuses = {
        "planner": "done",
        "coder": "done",
        "reviewer": "done",
        "tester": "done" if auto_apply else "ready",
    }
    steps = build_steps(plan, statuses)

    summary = {
        "preview_targets": [current_path],
        "next_steps": [
            "Проверь preview content.",
            "Сделай Apply Patch в Code Workspace.",
            "Запусти Verify.",
        ] if not auto_apply else [
            "Preview рассчитан.",
            "Подтверди Apply Patch.",
            "Сразу после apply выполни Verify.",
        ],
        "auto_apply": auto_apply,
        "changed_vs_disk": changed_vs_disk,
        "diff_stats": diff_stats,
    }

    result = {
        "status": "ok",
        "goal": goal,
        "mode": "code",
        "current_path": current_path,
        "plan": plan,
        "steps": steps,
        "summary": summary,
        "preview": {
            "path": current_path,
            "current_content": disk_content,
            "proposed_content": proposed_content,
            "changed_vs_disk": changed_vs_disk,
        },
        "verify": {
            "path": current_path,
            "checks": [
                "Файл существует",
                "Файл читается как UTF-8",
                "Preview рассчитан для текущего файла",
                "Готов к Verify после Apply",
            ],
        },
        "created_at": datetime.utcnow().isoformat(),
    }

    run_id = persist_run(
        goal,
        "code",
        current_path,
        "executed-preview",
        plan,
        steps,
        result,
    )
    result["run_id"] = run_id
    return result
