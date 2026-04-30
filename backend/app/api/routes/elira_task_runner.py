"""HTTP layer for the Elira task-runner endpoints.

All business/storage logic lives in
``app.application.elira_task_runner.runtime``; this module keeps only
the FastAPI router, the Pydantic request schema, and the thin delegating
handlers.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.application.elira_task_runner import runtime as task_runtime

router = APIRouter(prefix="/api/elira/task", tags=["elira-task-runner"])


class TaskRunPayload(BaseModel):
    goal: str = Field(min_length=1)
    mode: str = Field(default="code")
    current_path: Optional[str] = None
    staged_paths: List[str] = []


@router.post("/run")
def run_task(payload: TaskRunPayload):
    return task_runtime.prepare_run(
        payload.goal,
        payload.mode,
        payload.current_path,
        payload.staged_paths,
    )


@router.get("/history/list")
def list_task_history(limit: int = 30):
    return task_runtime.list_runs(limit)


@router.get("/history/get")
def get_task_history(id: int):
    result = task_runtime.get_run(id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Task run not found")
    return result
