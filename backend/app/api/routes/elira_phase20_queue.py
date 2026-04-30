"""HTTP layer for the Elira phase20 preview-queue endpoint.

All business logic lives in
``app.application.elira_phase20_queue.runtime``; this module keeps only
the FastAPI router, the Pydantic request schema, and the thin delegating
handler.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.elira_phase20_queue import runtime as queue_runtime

router = APIRouter(prefix="/api/elira/phase20", tags=["elira-phase20-queue"])


class PreviewQueuePayload(BaseModel):
    goal: str = Field(min_length=1)
    targets: List[str] = []


@router.post("/preview-queue")
def preview_queue(payload: PreviewQueuePayload):
    return queue_runtime.build_preview_queue(payload.goal, payload.targets)
