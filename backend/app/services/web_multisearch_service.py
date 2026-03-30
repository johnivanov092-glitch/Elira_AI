"""
web_multisearch_service.py — обёртка над core/web.py для API-роутеров.

Предоставляет:
  - multi_search(query, engines, max_results) — мульти-поиск с дедупликацией
  - deep_search(query, ...) — поиск + параллельная загрузка страниц
  - news_search(query, max_results) — DDG News
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def multi_search(
    query: str,
    engines: tuple[str, ...] = ("tavily", "duckduckgo", "wikipedia"),
    max_results: int = 10,
    per_engine: int | None = None,
) -> dict[str, Any]:
    """Мульти-поиск через несколько поисковиков с дедупликацией."""
    try:
        from app.core.web import search_web, format_search_results
        results = search_web(query, max_results=max_results, engines=engines, per_engine=per_engine)
        engines_found = list({r.get("engine", "") for r in results if r.get("engine")})
        return {
            "ok": True,
            "query": query,
            "results": results,
            "count": len(results),
            "engines": engines_found,
            "formatted": format_search_results(results),
        }
    except Exception as e:
        logger.error(f"multi_search error: {e}")
        return {"ok": False, "error": str(e), "results": [], "count": 0}


def deep_search(
    query: str,
    engines: tuple[str, ...] = ("tavily", "duckduckgo", "wikipedia"),
    max_results: int = 8,
    pages_to_read: int = 3,
) -> dict[str, Any]:
    """Поиск + параллельная загрузка содержимого страниц."""
    try:
        from app.core.web import research_web
        text = research_web(query, max_results=max_results, pages_to_read=pages_to_read, engines=engines)
        return {
            "ok": True,
            "query": query,
            "content": text,
            "content_length": len(text),
        }
    except Exception as e:
        logger.error(f"deep_search error: {e}")
        return {"ok": False, "error": str(e), "content": ""}


def news_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Поиск свежих новостей через DDG News."""
    try:
        from app.core.web import search_news

        raw = search_news(query, max_results=max_results)

        items = []
        for n in raw:
            url = n.get("href") or n.get("url") or ""
            if url and url.startswith("http"):
                items.append({
                    "title": n.get("title", ""),
                    "url": url,
                    "snippet": n.get("body", ""),
                    "date": n.get("date", ""),
                    "source": n.get("source", ""),
                })
        return {"ok": True, "query": query, "items": items, "count": len(items)}
    except Exception as e:
        logger.error(f"news_search error: {e}")
        return {"ok": False, "error": str(e), "items": [], "count": 0}


def fetch_page(url: str, max_chars: int = 10000) -> dict[str, Any]:
    """Загрузка и извлечение текста одной страницы."""
    try:
        from app.core.web import fetch_page_text
        text = fetch_page_text(url)
        if text and max_chars and len(text) > max_chars:
            text = text[:max_chars]
        return {"ok": True, "url": url, "text": text, "length": len(text) if text else 0}
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e), "text": ""}


class WebMultiSearchService:
    def search(self, query: str, max_results: int = 10, engines: tuple[str, ...] | None = None) -> dict[str, Any]:
        return multi_search(query, engines=engines or ("tavily", "duckduckgo", "wikipedia"), max_results=max_results)

    def deep_search(
        self,
        query: str,
        max_results: int = 8,
        pages_to_read: int = 3,
        engines: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        return deep_search(query, engines=engines or ("tavily", "duckduckgo", "wikipedia"), max_results=max_results, pages_to_read=pages_to_read)

    def news(self, query: str, max_results: int = 5) -> dict[str, Any]:
        return news_search(query, max_results=max_results)
