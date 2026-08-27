from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.application import memory
from app.application.local_models import list_local_models


router = APIRouter(prefix="/api/chat-agent", tags=["chat-agent"])

# Ф1 — unified facts store; Ф2 — through the MemoryService facade.
# "Память чата" (Settings) is backed by the same facts store the agent's
# `search_memory` tool reads, reached via the single `memory` facade (the
# "one door"). Scoped to the default profile — persona-scoping is a later phase.
_PROFILE = memory.default_profile()


class MemoryCreateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    category: str = "fact"
    source: str = "manual"
    importance: int = Field(default=5, ge=1, le=10)
    replaces_id: int | None = Field(default=None, ge=1)


class MemorySearchRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=20, ge=1, le=50)


@router.get("/models")
async def models():
    return await list_local_models()


@router.get("/memory")
def memory_list(limit: int = Query(default=100, ge=1, le=500)):
    result = memory.list_facts(limit=limit, profile=_PROFILE)
    return {"ok": True, "items": result.get("items", [])}


@router.get("/memory/stats")
def memory_stats():
    return memory.fact_stats(profile=_PROFILE)


@router.post("/memory")
def memory_add(payload: MemoryCreateRequest):
    result = memory.add_fact(
        payload.text,
        category=payload.category,
        source=payload.source,
        importance=payload.importance,
        profile=_PROFILE,
        replaces_id=payload.replaces_id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Не удалось сохранить факт"))
    return {"ok": True, "item": result}


@router.post("/memory/search")
def memory_search(payload: MemorySearchRequest):
    result = memory.search_facts(payload.query, limit=payload.limit, profile=_PROFILE)
    return {"ok": True, "items": result.get("items", [])}


@router.delete("/memory/{memory_id}")
def memory_delete(memory_id: str):
    try:
        mem_id = int(memory_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный id памяти")
    result = memory.delete_fact(mem_id, profile=_PROFILE)
    return {"ok": bool(result.get("ok"))}
