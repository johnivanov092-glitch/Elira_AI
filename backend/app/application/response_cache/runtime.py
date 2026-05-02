# -*- coding: utf-8 -*-
"""Application-layer runtime for the Response Cache.

Owns _DB_PATH, TTL/size constants, connection wiring, and all wrapper
functions that delegate to ``application/response_cache/{store,policy}``.
Pure Python — no HTTP, no FastAPI imports.
"""
from __future__ import annotations

import sqlite3

from app.application.response_cache import policy as cache_policy
from app.application.response_cache import store as cache_store
from app.core.config import DATA_DIR
from app.infrastructure.db.connection import connect_sqlite
from app.services.temporal_intent import detect_temporal_intent


_DB_PATH = DATA_DIR / "response_cache.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

CACHE_TTL = 7200
MAX_CACHE_SIZE = 500


# ── low-level helpers ─────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    return connect_sqlite(_DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def _init_db() -> None:
    cache_store.init_db(connect_func=_connect)


def _normalize_query(text: str) -> str:
    return cache_policy.normalize_query(text)


def _query_hash(normalized: str, model: str, profile: str) -> str:
    return cache_policy.query_hash(normalized, model, profile)


# ── public API ────────────────────────────────────────────────────────────────

def get_cached(
    query: str,
    model_name: str,
    profile_name: str,
) -> str | None:
    return cache_store.get_cached(
        connect_func=_connect,
        normalize_query_func=_normalize_query,
        query_hash_func=_query_hash,
        cache_ttl=CACHE_TTL,
        query=query,
        model_name=model_name,
        profile_name=profile_name,
    )


def set_cached(
    query: str,
    model_name: str,
    profile_name: str,
    response: str,
) -> None:
    cache_store.set_cached(
        connect_func=_connect,
        normalize_query_func=_normalize_query,
        query_hash_func=_query_hash,
        max_cache_size=MAX_CACHE_SIZE,
        query=query,
        model_name=model_name,
        profile_name=profile_name,
        response=response,
    )


def should_cache(query: str, route: str) -> bool:
    return cache_policy.should_cache_query(
        query=query,
        route=route,
        detect_temporal_intent_func=detect_temporal_intent,
    )


def clear_cache() -> None:
    cache_store.clear_cache(connect_func=_connect)


def cache_stats() -> dict:
    return cache_store.cache_stats(
        connect_func=_connect,
        max_cache_size=MAX_CACHE_SIZE,
        cache_ttl=CACHE_TTL,
    )


# ── module-level bootstrap ────────────────────────────────────────────────────

_init_db()
