from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.elira_phase20_state import runtime as state_runtime

router = APIRouter(prefix="/api/elira/phase20", tags=["elira-phase20-state"])


class Phase20StatePayload(BaseModel):
    goal: str = Field(min_length=1)
    queue_items: List[dict] = []
    staged_paths: List[str] = []


@router.post("/execution-state")
def build_execution_state(payload: Phase20StatePayload):
    return state_runtime.prepare_execution_state(
        payload.goal,
        payload.queue_items,
        payload.staged_paths,
    )


@router.get("/execution-state/list")
def list_execution_states(limit: int = 30):
    return state_runtime.list_states(limit)
