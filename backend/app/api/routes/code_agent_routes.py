"""HTTP entrypoint for the real (tool-using) code agent."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.code_agent.agent_loop import (
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
    run_code_agent,
)

router = APIRouter(prefix="/api/code-agent", tags=["code-agent"])


class CodeAgentRequest(BaseModel):
    message: str = Field(..., description="User task for the code agent")
    project_root: str = Field(..., description="Absolute path to the project directory")
    model: str = Field(default=DEFAULT_MODEL)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=50)


class CodeAgentResponse(BaseModel):
    ok: bool
    response: str
    steps: int
    tool_calls: list
    stop_reason: str
    error: Optional[str] = None


@router.post("/run", response_model=CodeAgentResponse)
def run(payload: CodeAgentRequest) -> CodeAgentResponse:
    result = run_code_agent(
        user_message=payload.message,
        project_root=payload.project_root,
        model=payload.model,
        max_steps=payload.max_steps,
    )
    return CodeAgentResponse(**result)
