"""Autopipeline compatibility facade."""
from __future__ import annotations

from app.application.autopipeline.runtime import (
    DB_PATH,
    _connect,
    _execute_task,
    _init_db,
    create_pipeline,
    delete_pipeline,
    get_pipeline,
    get_pipeline_logs,
    list_pipelines,
    run_pipeline_now,
    scheduler_status,
    start_scheduler,
    stop_scheduler,
    update_pipeline,
)

__all__ = [
    "DB_PATH",
    "_connect",
    "_execute_task",
    "_init_db",
    "create_pipeline",
    "delete_pipeline",
    "get_pipeline",
    "get_pipeline_logs",
    "list_pipelines",
    "run_pipeline_now",
    "scheduler_status",
    "start_scheduler",
    "stop_scheduler",
    "update_pipeline",
]
