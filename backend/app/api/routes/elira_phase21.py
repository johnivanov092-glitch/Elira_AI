"""HTTP layer for the Elira phase21 autonomous-controller endpoints.

All business/storage logic lives in
``app.application.elira_phase21.runtime``; this module keeps only the
FastAPI router, the Pydantic request schema, and the thin delegating
handlers.
"""
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
        payload.queue_items or [],
        payload.execution_state or {},
    )


@router.get("/history/list")
def list_phase21_history(limit: int = 30):
    return phase21_runtime.list_runs(limit)


@router.get("/history/get")
def get_phase21_history(id: int):
    return phase21_runtime.get_run(id)
