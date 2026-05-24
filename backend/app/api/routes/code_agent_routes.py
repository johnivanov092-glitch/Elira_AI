"""HTTP entrypoint for the tool-using code agent.

Three endpoints:
  POST /api/code-agent/run       — legacy single-shot (drain stream, return dict)
  POST /api/code-agent/stream    — SSE: yields run_started / step_started /
                                   tool_call / final_response / done events
  POST /api/code-agent/cancel    — flip the cancel flag for a running run_id
  GET  /api/code-agent/project-prompt?project_root=...  — read .elira/agent.md
  PUT  /api/code-agent/project-prompt                   — write it
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.application.code_agent.agent_loop import (
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
    DEFAULT_NUM_CTX,
    get_project_prompt,
    request_cancel,
    run_code_agent,
    set_project_prompt,
    stream_code_agent,
    summarize_history,
)

router = APIRouter(prefix="/api/code-agent", tags=["code-agent"])


class ConversationMessage(BaseModel):
    role: str = Field(..., description="user or assistant")
    content: str


class CodeAgentRequest(BaseModel):
    message: str = Field(..., description="User task for the code agent")
    project_root: str = Field(..., description="Absolute path to the project directory")
    model: str = Field(default=DEFAULT_MODEL)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=50)
    num_ctx: int = Field(default=DEFAULT_NUM_CTX, ge=1024, le=131072)
    conversation_history: list[ConversationMessage] | None = None


class CodeAgentResponse(BaseModel):
    ok: bool
    response: str
    steps: int
    tool_calls: list
    stop_reason: str
    error: Optional[str] = None


class CodeAgentStreamRequest(CodeAgentRequest):
    run_id: Optional[str] = Field(default=None, description="Client-provided ID; needed if you want to /cancel later")


class CodeAgentCancelRequest(BaseModel):
    run_id: str


class ProjectPromptWriteRequest(BaseModel):
    project_root: str
    content: str


class SummarizeHistoryRequest(BaseModel):
    messages: list[ConversationMessage]
    model: str = Field(default=DEFAULT_MODEL)
    num_ctx: int = Field(default=DEFAULT_NUM_CTX, ge=1024, le=131072)


class SummarizeHistoryResponse(BaseModel):
    ok: bool
    summary: str
    turn_count: int
    error: Optional[str] = None


@router.post("/run", response_model=CodeAgentResponse)
def run(payload: CodeAgentRequest) -> CodeAgentResponse:
    history = [m.model_dump() for m in (payload.conversation_history or [])]
    result = run_code_agent(
        user_message=payload.message,
        project_root=payload.project_root,
        model=payload.model,
        max_steps=payload.max_steps,
        conversation_history=history,
        num_ctx=payload.num_ctx,
    )
    return CodeAgentResponse(**result)


def _sse_format(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/stream")
def stream(payload: CodeAgentStreamRequest) -> StreamingResponse:
    run_id = payload.run_id or uuid.uuid4().hex
    history = [m.model_dump() for m in (payload.conversation_history or [])]

    def gen():
        try:
            for event in stream_code_agent(
                user_message=payload.message,
                project_root=payload.project_root,
                model=payload.model,
                max_steps=payload.max_steps,
                conversation_history=history,
                num_ctx=payload.num_ctx,
                run_id=run_id,
            ):
                yield _sse_format(event)
        except Exception as exc:
            yield _sse_format({
                "type": "done",
                "ok": False,
                "steps": 0,
                "stop_reason": "error",
                "error": str(exc),
            })

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Run-Id": run_id,
        },
    )


@router.post("/cancel")
def cancel(payload: CodeAgentCancelRequest) -> dict[str, Any]:
    found = request_cancel(payload.run_id)
    return {"ok": True, "found": found, "run_id": payload.run_id}


@router.get("/project-prompt")
def read_project_prompt(project_root: str) -> dict[str, Any]:
    if not project_root:
        raise HTTPException(status_code=400, detail="project_root is required")
    return get_project_prompt(project_root)


@router.put("/project-prompt")
def write_project_prompt(payload: ProjectPromptWriteRequest) -> dict[str, Any]:
    result = set_project_prompt(payload.project_root, payload.content)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "failed to write"))
    return result


@router.post("/summarize-history", response_model=SummarizeHistoryResponse)
def summarize(payload: SummarizeHistoryRequest) -> SummarizeHistoryResponse:
    messages = [m.model_dump() for m in payload.messages]
    result = summarize_history(messages=messages, model=payload.model, num_ctx=payload.num_ctx)
    return SummarizeHistoryResponse(**result)
