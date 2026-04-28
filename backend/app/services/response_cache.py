"""Compatibility facade for response cache runtime."""
from __future__ import annotations

from app.application.response_cache.runtime import (
    CACHE_TTL,
    MAX_CACHE_SIZE,
    _connect,
    _init_db,
    _normalize_query,
    _query_hash,
    cache_stats,
    clear_cache,
    get_cached,
    set_cached,
    should_cache,
)

__all__ = [
    "CACHE_TTL",
    "MAX_CACHE_SIZE",
    "_connect",
    "_init_db",
    "_normalize_query",
    "_query_hash",
    "cache_stats",
    "clear_cache",
    "get_cached",
    "set_cached",
    "should_cache",
]
