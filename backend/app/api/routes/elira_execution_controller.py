from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.elira_execution_controller import runtime as controller_runtime

router = APIRouter(prefix="/api/elira/phase21", tags=["elira-execution-controller"])


class ExecutionControllerRunPayload(BaseModel):
    goal: str = Field(min_length=1)
    queue_items: List[dict] = []
    execution_state: dict = {}


@router.post("/run")
def run_execution_controller(payload: ExecutionControllerRunPayload):
    return controller_runtime.prepare_run(
        payload.goal,
        payload.queue_items,
        payload.execution_state,
    )


@router.get("/history/list")
def list_execution_controller_history(limit: int = 30):
    return controller_runtime.list_runs(limit)


@router.get("/history/get")
def get_execution_controller_history(id: int):
    return controller_runtime.get_run(id)
