"""Task planner compatibility facade."""

from __future__ import annotations

from app.application.task_planner.service import (
    DB_PATH,
    PRIORITIES,
    STATUSES,
    _connect,
    _init_db,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    task_stats,
    update_task,
)

__all__ = [
    "DB_PATH",
    "PRIORITIES",
    "STATUSES",
    "_connect",
    "_init_db",
    "create_task",
    "delete_task",
    "get_task",
    "list_tasks",
    "task_stats",
    "update_task",
]
