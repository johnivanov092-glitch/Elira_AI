"""Response Cache — compatibility shim.

All logic lives in ``app.application.response_cache.runtime``.
Public API re-exported for all callers.
"""
from __future__ import annotations

from app.application.response_cache.runtime import (
    CACHE_TTL,
    MAX_CACHE_SIZE,
    cache_stats,
    clear_cache,
    get_cached,
    set_cached,
    should_cache,
)

__all__ = [
    "CACHE_TTL",
    "MAX_CACHE_SIZE",
    "cache_stats",
    "clear_cache",
    "get_cached",
    "set_cached",
    "should_cache",
]
