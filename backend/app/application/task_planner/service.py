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
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def _init_db():
    planner_runtime.init_db(connect_func=_connect)


_init_db()

PRIORITIES = ["low", "medium", "high", "urgent"]
STATUSES = ["todo", "in_progress", "done", "cancelled"]


def create_task(
    title: str,
    description: str = "",
    category: str = "general",
    priority: str = "medium",
    due_date: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    return planner_runtime.create_task(
        connect_func=_connect,
        id_func=lambda: str(uuid.uuid4())[:8],
        now_func=lambda: datetime.utcnow().isoformat(),
        title=title,
        description=description,
        category=category,
        priority=priority,
        due_date=due_date,
        tags=tags,
    )


def list_tasks(status: str | None = None, category: str | None = None, limit: int = 100) -> dict:
    return planner_runtime.list_tasks(
        connect_func=_connect,
        status=status,
        category=category,
        limit=limit,
    )


def get_task(tid: str) -> dict:
    return planner_runtime.get_task(connect_func=_connect, tid=tid)


def update_task(tid: str, **kwargs) -> dict:
    return planner_runtime.update_task(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
        tid=tid,
        **kwargs,
    )


def delete_task(tid: str) -> dict:
    return planner_runtime.delete_task(connect_func=_connect, tid=tid)


def task_stats() -> dict:
    return planner_runtime.task_stats(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
    )


def bump_retry(tid: str, backoff_base_seconds: int = 60) -> dict:
    return planner_runtime.bump_retry(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
        tid=tid,
        backoff_base_seconds=backoff_base_seconds,
    )


def set_waiting_approval(tid: str) -> dict:
    return planner_runtime.set_waiting_approval(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
        tid=tid,
    )


def recover_stale_tasks(
    *,
    stale_after_seconds: int = 3600,
    limit: int = 50,
    backoff_base_seconds: int = 60,
) -> dict:
    from app.application.event_bus.runtime import emit_event

    return planner_runtime.recover_stale_tasks(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
        stale_after_seconds=stale_after_seconds,
        limit=limit,
        backoff_base_seconds=backoff_base_seconds,
        emit_event_func=emit_event,
    )


def list_checklist(run_id: str) -> dict:
    return planner_runtime.list_checklist(
        connect_func=_connect,
        run_id=run_id,
    )


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
        now_func=lambda: datetime.utcnow().isoformat(),
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
    max_steps: int = 0,
    max_context_tokens: int = 0,
    tool_allowlist: list[str] | tuple[str, ...] | None = None,
) -> dict:
    from app.application.event_bus.runtime import emit_event

    return planner_runtime.start_subagent_run(
        connect_func=_connect,
        id_func=lambda: f"sub-{uuid.uuid4().hex}",
        now_func=lambda: datetime.utcnow().isoformat(),
        parent_run_id=parent_run_id,
        role=role,
        task=task,
        depth=depth,
        max_steps=max_steps,
        max_context_tokens=max_context_tokens,
        tool_allowlist=tool_allowlist,
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
        now_func=lambda: datetime.utcnow().isoformat(),
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
