"""
terminal.py — terminal endpoints for the Code tab.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.infrastructure.shell.terminal_service import exec_command, change_dir, get_cwd

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


class ExecRequest(BaseModel):
    command: str
    cwd: str = ""


class CdRequest(BaseModel):
    path: str


@router.post("/exec")
def route_exec(payload: ExecRequest):
    return exec_command(payload.command, payload.cwd)


@router.get("/cwd")
def route_cwd():
    return {"ok": True, "cwd": get_cwd()}


@router.post("/cd")
def route_cd(payload: CdRequest):
    return change_dir(payload.path)
