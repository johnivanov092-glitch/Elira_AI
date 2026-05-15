"""Thin facade — all response cache logic lives in infrastructure/cache/response_cache.py."""
from app.infrastructure.cache.response_cache import (  # noqa: F401
    CACHE_TTL,
    MAX_CACHE_SIZE,
    _DB_PATH,
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
