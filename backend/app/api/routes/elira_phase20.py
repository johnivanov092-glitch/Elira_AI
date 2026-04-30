"""HTTP layer for the Elira phase20 multi-agent dev-loop endpoints.

All business/storage logic lives in
``app.application.elira_phase20.runtime``; this module keeps only the
FastAPI router, the Pydantic request schema, and the thin delegating
handlers.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.elira_phase20 import runtime as phase20_runtime

router = APIRouter(prefix="/api/elira/phase20", tags=["elira-phase20"])


class Phase20RunPayload(BaseModel):
    goal: str = Field(min_length=1)
    selected_paths: List[str] = []


@router.post("/run")
def run_phase20(payload: Phase20RunPayload):
    return phase20_runtime.prepare_run(
        payload.goal,
        payload.selected_paths,
    )


@router.get("/history/list")
def list_phase20_history(limit: int = 30):
    return phase20_runtime.list_runs(limit)


@router.get("/history/get")
def get_phase20_history(id: int):
    return phase20_runtime.get_run(id)
