"""Compatibility facade for multi-engine web search helpers."""
from __future__ import annotations

from app.infrastructure.search.multisearch import (
    WebMultiSearchService,
    deep_search,
    fetch_page,
    multi_search,
    news_search,
)

__all__ = [
    "WebMultiSearchService",
    "deep_search",
    "fetch_page",
    "multi_search",
    "news_search",
]
