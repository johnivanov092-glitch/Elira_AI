"""
terminal.py - terminal endpoint for the Code tab.

Windows output decoding is handled in application.terminal.runtime.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.application.terminal import runtime as terminal_runtime

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


class ExecRequest(BaseModel):
    command: str
    cwd: str = ""


class CdRequest(BaseModel):
    path: str


@router.post("/exec")
def exec_command(payload: ExecRequest):
    return terminal_runtime.exec_command(payload.command, payload.cwd)


@router.get("/cwd")
def get_cwd():
    return terminal_runtime.get_cwd()


@router.post("/cd")
def change_dir(payload: CdRequest):
    return terminal_runtime.change_dir(payload.path)
