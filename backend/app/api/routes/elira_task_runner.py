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
        goal=payload.goal,
        mode=payload.mode,
        current_path=payload.current_path,
        staged_paths=payload.staged_paths,
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
