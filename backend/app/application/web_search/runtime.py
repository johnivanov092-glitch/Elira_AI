from __future__ import annotations

from typing import Any

from app.infrastructure.search.multisearch import (
    deep_search as deep_search_impl,
    fetch_page as fetch_page_impl,
    multi_search as multi_search_impl,
    news_search as news_search_impl,
)

DEFAULT_ENGINES = ("tavily", "duckduckgo", "wikipedia")
ENGINE_ITEMS = [
    {"id": "tavily", "name": "Tavily", "type": "research-api", "status": "active"},
    {"id": "duckduckgo", "name": "DuckDuckGo", "type": "search", "status": "active"},
    {"id": "wikipedia", "name": "Wikipedia", "type": "encyclopedia", "status": "active"},
]


def normalize_engines(engines: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    return tuple(engines) if engines else DEFAULT_ENGINES


def search(query: str, *, engines: list[str] | tuple[str, ...] | None = None, max_results: int = 10) -> dict[str, Any]:
    return multi_search_impl(
        query,
        engines=normalize_engines(engines),
        max_results=max_results,
    )


def deep_search(
    query: str,
    *,
    engines: list[str] | tuple[str, ...] | None = None,
    max_results: int = 8,
    pages_to_read: int = 3,
) -> dict[str, Any]:
    return deep_search_impl(
        query,
        engines=normalize_engines(engines),
        max_results=max_results,
        pages_to_read=pages_to_read,
    )


def news(query: str, *, max_results: int = 10) -> dict[str, Any]:
    return news_search_impl(query, max_results=max_results)


def fetch(url: str, *, max_chars: int = 10000) -> dict[str, Any]:
    return fetch_page_impl(url, max_chars=max_chars)


def list_engines() -> dict[str, Any]:
    return {
        "engines": list(ENGINE_ITEMS),
        "default": list(DEFAULT_ENGINES),
    }
