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
