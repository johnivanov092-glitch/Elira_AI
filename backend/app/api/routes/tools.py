from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.tool_service import list_tools, run_tool

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolRunRequest(BaseModel):
    tool_name: str = Field(..., min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


@router.get("")
def tools_list():
    return list_tools()


@router.get("/ping")
def ping():
    return {"status": "tools route active"}


@router.post("/run")
def tools_run(payload: ToolRunRequest):
    return run_tool(payload.tool_name, payload.args)
