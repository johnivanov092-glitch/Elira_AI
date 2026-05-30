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

# Infrastructure adapter extracted from app.services.web_multisearch_service.


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


def news_multi_search(
    query: str,
    max_results: int = 10,
    *,
    local_first: bool = False,
    geo_scope: str = "",
    preferred_domains: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """News search with per-engine diagnostics for scheduled pipelines."""
    from app.core.web import format_search_results
    from app.core.web_engines import KZ_LOCAL_NEWS_DOMAINS, search_duckduckgo, search_tavily
    from app.core.web_runtime import dedupe_results, rerank_results, search_news

    engines_attempted = ["tavily", "duckduckgo", "ddg-news"]
    engine_errors: dict[str, str] = {}
    combined: list[dict[str, str]] = []
    per_engine = max(3, max_results)
    domains = preferred_domains if preferred_domains is not None else (KZ_LOCAL_NEWS_DOMAINS if local_first else ())

    for engine, search_func in (
        ("tavily", search_tavily),
        ("duckduckgo", search_duckduckgo),
    ):
        try:
            combined.extend(search_func(query, max_results=per_engine))
        except Exception as exc:
            engine_errors[engine] = str(exc)

    try:
        combined.extend(
            search_news(
                query,
                max_results=per_engine,
                intent_kind="geo_news",
                geo_scope=geo_scope,
                local_first=local_first,
                preferred_domains=domains,
                raise_errors=True,
            )
        )
    except Exception as exc:
        engine_errors["ddg-news"] = str(exc)

    merged = dedupe_results(combined, max_results=max(max_results, per_engine * len(engines_attempted)))
    results = rerank_results(
        merged,
        intent_kind="geo_news",
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=domains,
    )[:max_results]
    engines_used = sorted({item.get("engine", "") for item in results if item.get("engine")})

    return {
        "ok": bool(results),
        "query": query,
        "mode": "local_news" if local_first else "news",
        "results": results,
        "count": len(results),
        "engines": engines_used,
        "engines_attempted": engines_attempted,
        "engines_used": engines_used,
        "engine_errors": engine_errors,
        "formatted": format_search_results(results),
    }


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
