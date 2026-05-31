"""Web search capability routes."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/web", tags=["web"])

_ENGINES = [
    {"id": "tavily", "name": "Tavily", "type": "research-api", "status": "active"},
    {"id": "duckduckgo", "name": "DuckDuckGo", "type": "search", "status": "active"},
    {"id": "wikipedia", "name": "Wikipedia", "type": "encyclopedia", "status": "active"},
]
_DEFAULT_ENGINES = ["tavily", "duckduckgo", "wikipedia"]


@router.get("/engines")
async def list_engines():
    """List available search engines and their defaults."""
    return {"engines": _ENGINES, "default": _DEFAULT_ENGINES}
