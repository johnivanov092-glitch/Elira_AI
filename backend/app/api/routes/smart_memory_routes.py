"""
smart_memory_routes.py — API для умной памяти.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from app.application.memory.smart_memory import (
    add_memory, list_memories, delete_memory, clear_all_memories,
    search_memory, get_stats, extract_and_save,
)

router = APIRouter(prefix="/api/smart-memory", tags=["smart-memory"])


class AddRequest(BaseModel):
    text: str
    category: str = "fact"
    importance: int = 5


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


@router.get("/list")
def api_list(category: Optional[str] = None, limit: int = 50):
    return list_memories(category=category, limit=limit)


@router.post("/add")
def api_add(payload: AddRequest):
    return add_memory(payload.text, category=payload.category, source="manual", importance=payload.importance)


@router.delete("/{mem_id}")
def api_delete(mem_id: int):
    return delete_memory(mem_id)


@router.delete("/")
def api_clear():
    return clear_all_memories()


@router.post("/search")
def api_search(payload: SearchRequest):
    return search_memory(payload.query, limit=payload.limit)


@router.get("/stats")
def api_stats():
    return get_stats()
