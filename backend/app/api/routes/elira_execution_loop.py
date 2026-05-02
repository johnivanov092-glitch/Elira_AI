from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.elira_execution_loop import runtime as execution_loop_runtime

router = APIRouter(prefix="/api/elira/phase20", tags=["elira-execution-loop"])


class ExecutionLoopRunPayload(BaseModel):
    goal: str = Field(min_length=1)
    selected_paths: List[str] = []


@router.post("/run")
def run_execution_loop(payload: ExecutionLoopRunPayload):
    return execution_loop_runtime.prepare_run(payload.goal, payload.selected_paths)


@router.get("/history/list")
def list_execution_loop_history(limit: int = 30):
    return execution_loop_runtime.list_runs(limit)


@router.get("/history/get")
def get_execution_loop_history(id: int):
    return execution_loop_runtime.get_run(id)
