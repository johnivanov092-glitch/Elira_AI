"""HTTP layer for the Elira execute + memory endpoints.

All business/storage logic lives in
``app.application.elira_execute.runtime``; this module keeps only the
FastAPI router, Pydantic request models, and the thin delegating
handlers.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.elira_execute import runtime as execute_runtime

router = APIRouter(prefix="/api/elira", tags=["elira-execute"])


class ExecutePayload(BaseModel):
    chat_id: Optional[str] = None
    content: str = Field(min_length=1)
    mode: str = Field(default="chat")
    model: Optional[str] = None
    agent_profile: Optional[str] = None


class MemorySavePayload(BaseModel):
    chat_id: Optional[str] = None
    title: Optional[str] = None
    content: str = Field(min_length=1)
    source: str = Field(default="chat")
    pinned: bool = False


class MemoryDeletePayload(BaseModel):
    id: int


@router.post("/execute")
def execute(payload: ExecutePayload):
    return execute_runtime.build_mode_reply(
        payload.content,
        payload.mode,
        payload.model,
        payload.agent_profile,
    )


@router.get("/memory/list")
def list_memory(q: str = ""):
    return execute_runtime.list_memory(q)


@router.post("/memory/save")
def save_memory(payload: MemorySavePayload):
    return execute_runtime.save_memory(
        payload.content,
        payload.chat_id,
        payload.title,
        payload.source,
        payload.pinned,
    )


@router.post("/memory/delete")
def delete_memory(payload: MemoryDeletePayload):
    return execute_runtime.delete_memory(payload.id)
