"""HTTP layer for the terminal endpoint.

All execution logic lives in ``app.application.terminal.runtime``.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.application.terminal import runtime as term_runtime

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


class ExecRequest(BaseModel):
    command: str
    cwd: str = ""


class CdRequest(BaseModel):
    path: str


@router.post("/exec")
def exec_command(payload: ExecRequest):
    return term_runtime.exec_command(payload.command, payload.cwd)


@router.get("/cwd")
def get_cwd():
    return {"ok": True, "cwd": term_runtime.get_cwd()}


@router.post("/cd")
def change_dir(payload: CdRequest):
    return term_runtime.change_dir(payload.path)
