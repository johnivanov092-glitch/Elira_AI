"""HTTP layer for the Elira phase19 multi-file dev-loop endpoints.

All business/storage logic lives in
``app.application.elira_phase19.runtime``; this module keeps only the
FastAPI router, the Pydantic request schema, and the thin delegating
handlers.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.elira_phase19 import runtime as phase19_runtime

router = APIRouter(prefix="/api/elira/phase19", tags=["elira-phase19"])


class Phase19RunPayload(BaseModel):
    goal: str = Field(min_length=1)
    mode: str = Field(default="multi-file")
    selected_paths: List[str] = []


@router.post("/run")
def run_phase19(payload: Phase19RunPayload):
    return phase19_runtime.prepare_run(
        payload.goal,
        payload.mode,
        payload.selected_paths,
    )


@router.get("/history/list")
def list_phase19_history(limit: int = 30):
    return phase19_runtime.list_runs(limit)


@router.get("/history/get")
def get_phase19_history(id: int):
    return phase19_runtime.get_run(id)
