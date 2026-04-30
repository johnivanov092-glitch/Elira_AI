"""HTTP layer for the Elira supervisor.

All business/storage logic lives in
``app.application.elira_supervisor.runtime``; this module keeps only the
FastAPI router, Pydantic request models, and the thin glue that
translates runtime errors into HTTP responses.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.application.elira_supervisor import runtime as supervisor_runtime

router = APIRouter(prefix="/api/elira/supervisor", tags=["elira-supervisor"])


class SupervisorRunPayload(BaseModel):
    goal: str = Field(min_length=1)
    mode: str = Field(default="code")
    current_path: Optional[str] = None
    staged_paths: List[str] = []
    auto_apply: bool = False


class SupervisorExecutePayload(BaseModel):
    goal: str = Field(min_length=1)
    current_path: str = Field(min_length=1)
    current_content: str = ""
    auto_apply: bool = False


_PATH_ERROR_HTTP = {
    "outside_root": (403, "Path is outside project root"),
    "blocked": (403, "Path points to blocked area"),
}


def resolve_project_path(rel_path: str) -> Path:
    """FastAPI-bound wrapper around the runtime path resolver."""
    target, error = supervisor_runtime.resolve_project_path(rel_path)
    if error is not None:
        status, detail = _PATH_ERROR_HTTP.get(error, (400, "Invalid path"))
        raise HTTPException(status_code=status, detail=detail)
    assert target is not None
    return target


@router.post("/run")
def run_supervisor(payload: SupervisorRunPayload):
    return supervisor_runtime.prepare_run(
        payload.goal,
        payload.mode,
        payload.current_path,
        payload.staged_paths,
        payload.auto_apply,
    )


@router.post("/execute")
def execute_supervisor(payload: SupervisorExecutePayload):
    target = resolve_project_path(payload.current_path)
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Path points to directory")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Target file not found")

    return supervisor_runtime.prepare_execute(
        payload.goal,
        target,
        payload.current_path,
        payload.current_content,
        payload.auto_apply,
    )


@router.get("/history/list")
def list_supervisor_history(limit: int = 30):
    return supervisor_runtime.list_runs(limit)


@router.get("/history/get")
def get_supervisor_history(id: int):
    return supervisor_runtime.get_run(id)
