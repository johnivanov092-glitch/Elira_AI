"""HTTP layer for the Elira multi-file dev-loop endpoints.

All business/storage logic lives in
``app.application.elira_multi_file_loop.runtime``; this module keeps only the
FastAPI router, the Pydantic request schema, and the thin delegating
handlers.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.elira_multi_file_loop import runtime as multi_file_loop_runtime

router = APIRouter(prefix="/api/elira/phase19", tags=["elira-multi-file-loop"])


class MultiFileLoopRunPayload(BaseModel):
    goal: str = Field(min_length=1)
    mode: str = Field(default="multi-file")
    selected_paths: List[str] = []


@router.post("/run")
def run_multi_file_loop(payload: MultiFileLoopRunPayload):
    return multi_file_loop_runtime.prepare_run(
        payload.goal,
        payload.mode,
        payload.selected_paths,
    )


@router.get("/history/list")
def list_multi_file_loop_history(limit: int = 30):
    return multi_file_loop_runtime.list_runs(limit)


@router.get("/history/get")
def get_multi_file_loop_history(id: int):
    return multi_file_loop_runtime.get_run(id)
