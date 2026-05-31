from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.application.web_search import runtime as web_search_runtime

router = APIRouter(prefix="/api/web", tags=["web-search"])


class SearchRequest(BaseModel):
    query: str
    engines: list[str] | None = None
    max_results: int = 10


class DeepSearchRequest(BaseModel):
    query: str
    engines: list[str] | None = None
    max_results: int = 8
    pages_to_read: int = 3


class FetchRequest(BaseModel):
    url: str
    max_chars: int = 10000


@router.post("/search")
async def web_search(req: SearchRequest):
    return web_search_runtime.search(
        req.query,
        engines=req.engines,
        max_results=req.max_results,
    )


@router.post("/deep-search")
async def web_deep_search(req: DeepSearchRequest):
    return web_search_runtime.deep_search(
        req.query,
        engines=req.engines,
        max_results=req.max_results,
        pages_to_read=req.pages_to_read,
    )


@router.post("/news")
async def web_news(req: SearchRequest):
    return web_search_runtime.news(req.query, max_results=req.max_results)


@router.post("/fetch")
async def web_fetch(req: FetchRequest):
    return web_search_runtime.fetch(req.url, max_chars=req.max_chars)


@router.get("/engines")
async def list_engines():
    return web_search_runtime.list_engines()
