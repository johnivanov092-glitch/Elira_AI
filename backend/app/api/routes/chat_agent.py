from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.application.chat_agent import state
from app.application.local_models import list_local_models


router = APIRouter(prefix="/api/chat-agent", tags=["chat-agent"])


class MemoryCreateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    category: str = "fact"
    source: str = "manual"
    importance: int = Field(default=5, ge=1, le=10)


class MemorySearchRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=20, ge=1, le=50)


@router.get("/models")
async def models():
    return await list_local_models()


@router.get("/memory")
def memory_list(limit: int = Query(default=100, ge=1, le=500)):
    return {"ok": True, "items": state.list_memory(limit)}


@router.get("/memory/stats")
def memory_stats():
    return state.memory_stats()


@router.post("/memory")
def memory_add(payload: MemoryCreateRequest):
    try:
        item = state.add_memory(
            payload.text,
            category=payload.category,
            source=payload.source,
            importance=payload.importance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": item}


@router.post("/memory/search")
def memory_search(payload: MemorySearchRequest):
    return {"ok": True, "items": state.search_memory(payload.query, limit=payload.limit)}


@router.delete("/memory/{memory_id}")
def memory_delete(memory_id: str):
    return {"ok": state.delete_memory(memory_id)}
