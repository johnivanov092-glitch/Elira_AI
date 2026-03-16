from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas.memory import MemoryAddRequest, MemorySearchRequest
from app.services.memory_service import (
    add_memory,
    build_memory_context,
    delete_memory,
    list_memories,
    list_profiles,
    search_memory,
)

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/profiles")
def memory_profiles():
    return JSONResponse(content=list_profiles(), media_type="application/json; charset=utf-8")


@router.get("/items/{profile}")
def memory_items(profile: str):
    return JSONResponse(content=list_memories(profile), media_type="application/json; charset=utf-8")


@router.post("/add")
def memory_add(payload: MemoryAddRequest):
    return JSONResponse(
        content=add_memory(payload.profile, payload.text, payload.source),
        media_type="application/json; charset=utf-8",
    )


@router.post("/search")
def memory_search(payload: MemorySearchRequest):
    return JSONResponse(
        content=search_memory(payload.profile, payload.query, payload.limit),
        media_type="application/json; charset=utf-8",
    )


@router.get("/context/{profile}")
def memory_context(profile: str, query: str, limit: int = 5):
    return JSONResponse(
        content={"ok": True, "profile": profile, "context": build_memory_context(profile, query, limit)},
        media_type="application/json; charset=utf-8",
    )


@router.delete("/items/{profile}/{item_id}")
def memory_delete(profile: str, item_id: str):
    return JSONResponse(
        content=delete_memory(profile, item_id),
        media_type="application/json; charset=utf-8",
    )
