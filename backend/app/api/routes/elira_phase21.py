from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.elira_phase21 import runtime as phase21_runtime

router = APIRouter(prefix="/api/elira/phase21", tags=["elira-phase21"])


class Phase21RunPayload(BaseModel):
    goal: str = Field(min_length=1)
    queue_items: List[dict] = []
    execution_state: dict = {}


@router.post("/run")
def run_phase21(payload: Phase21RunPayload):
    return phase21_runtime.prepare_run(
        payload.goal,
        payload.queue_items,
        payload.execution_state,
    )


@router.get("/history/list")
def list_phase21_history(limit: int = 30):
    return phase21_runtime.list_runs(limit)


@router.get("/history/get")
def get_phase21_history(id: int):
    return phase21_runtime.get_run(id)
