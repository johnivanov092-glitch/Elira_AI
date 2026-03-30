"""API роуты для Web Search Pro — мульти-поиск, новости, fetch страниц."""
from fastapi import APIRouter, Query
from pydantic import BaseModel

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
    """Мульти-поиск: DDG + Bing + Google (с дедупликацией)."""
    from app.services.web_multisearch_service import multi_search
    engines = tuple(req.engines) if req.engines else ("tavily", "duckduckgo", "wikipedia")
    return multi_search(req.query, engines=engines, max_results=req.max_results)


@router.post("/deep-search")
async def web_deep_search(req: DeepSearchRequest):
    """Поиск + параллельная загрузка содержимого страниц."""
    from app.services.web_multisearch_service import deep_search
    engines = tuple(req.engines) if req.engines else ("tavily", "duckduckgo", "wikipedia")
    return deep_search(req.query, engines=engines, max_results=req.max_results, pages_to_read=req.pages_to_read)


@router.post("/news")
async def web_news(req: SearchRequest):
    """Поиск свежих новостей через DDG News."""
    from app.services.web_multisearch_service import news_search
    return news_search(req.query, max_results=req.max_results)


@router.post("/fetch")
async def web_fetch(req: FetchRequest):
    """Загрузка и извлечение текста одной страницы."""
    from app.services.web_multisearch_service import fetch_page
    return fetch_page(req.url, max_chars=req.max_chars)


@router.get("/engines")
async def list_engines():
    """Список доступных поисковых движков."""
    return {
        "engines": [
            {"id": "tavily", "name": "Tavily", "type": "research-api", "status": "active"},
            {"id": "duckduckgo", "name": "DuckDuckGo", "type": "search", "status": "active"},
            {"id": "wikipedia", "name": "Wikipedia", "type": "encyclopedia", "status": "active"},
        ],
        "default": ["tavily", "duckduckgo", "wikipedia"],
    }
