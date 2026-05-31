"""HTTP endpoint for the Spotlight Cmd+K global search overlay."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.application.spotlight import search_everywhere


router = APIRouter(prefix="/api/spotlight", tags=["spotlight"])


@router.get("/search")
def spotlight_search(q: str = Query("", min_length=0, max_length=300)) -> dict[str, Any]:
    """Fan out a query to chats / code-agent sessions / RAG / library.

    Returns four grouped buckets so the UI can render them with their
    own headers + icons. Empty buckets are still present (empty list)
    so the client doesn't have to do existence checks.

    A query under 2 chars yields all-empty buckets — too short to be
    discriminating and would just spam noise.
    """
    return search_everywhere(q)
