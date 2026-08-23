from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from app.application.task_planner import runtime as planner_runtime
from app.core.config import DATA_DIR
from app.infrastructure.db.connection import connect_sqlite


DB_PATH = DATA_DIR / "task_planner.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _connect():
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row)


def _now() -> str:
    return datetime.utcnow().isoformat()


planner_runtime.init_db(connect_func=_connect)


def list_checklist(run_id: str) -> dict:
    return planner_runtime.list_checklist(connect_func=_connect, run_id=run_id)


def todo_update(
    *,
    run_id: str,
    items: list[dict] | None = None,
    updates: list[dict] | None = None,
) -> dict:
    from app.application.event_bus.runtime import emit_event

    return planner_runtime.update_checklist(
        connect_func=_connect,
        id_func=lambda: str(uuid.uuid4())[:8],
        now_func=_now,
        run_id=run_id,
        items=items,
        updates=updates,
        emit_event_func=emit_event,
    )


def start_subagent_run(
    *,
    parent_run_id: str,
    role: str,
    task: str,
    depth: int = 0,
    max_context_tokens: int = 0,
) -> dict:
    from app.application.event_bus.runtime import emit_event

    return planner_runtime.start_subagent_run(
        connect_func=_connect,
        id_func=lambda: f"sub-{uuid.uuid4().hex}",
        now_func=_now,
        parent_run_id=parent_run_id,
        role=role,
        task=task,
        depth=depth,
        max_context_tokens=max_context_tokens,
        emit_event_func=emit_event,
    )


def finish_subagent_run(
    *,
    subagent_run_id: str,
    status: str,
    result_text: str = "",
    error: str = "",
) -> dict:
    from app.application.event_bus.runtime import emit_event

    return planner_runtime.finish_subagent_run(
        connect_func=_connect,
        now_func=_now,
        subagent_run_id=subagent_run_id,
        status=status,
        result_text=result_text,
        error=error,
        emit_event_func=emit_event,
    )


def get_subagent_run(subagent_run_id: str) -> dict:
    return planner_runtime.get_subagent_run(
        connect_func=_connect,
        subagent_run_id=subagent_run_id,
    )


def list_subagent_runs(parent_run_id: str, limit: int = 100) -> dict:
    return planner_runtime.list_subagent_runs(
        connect_func=_connect,
        parent_run_id=parent_run_id,
        limit=limit,
    )
