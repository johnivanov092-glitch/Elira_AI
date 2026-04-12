"""SQLite response cache with TTL and temporal freshness awareness."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time

from app.core.config import DATA_DIR
from app.infrastructure.db.connection import connect_sqlite
from app.services.temporal_intent import detect_temporal_intent


_DB_PATH = DATA_DIR / "response_cache.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

CACHE_TTL = 7200
MAX_CACHE_SIZE = 500


def _connect() -> sqlite3.Connection:
    return connect_sqlite(
        _DB_PATH,
        row_factory=sqlite3.Row,
        journal_mode=None,
    )


def _init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT NOT NULL UNIQUE,
                query_normalized TEXT NOT NULL,
                model_name TEXT NOT NULL,
                profile_name TEXT NOT NULL,
                response TEXT NOT NULL,
                created_at REAL NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_hash ON cache(query_hash)")
        conn.commit()
    finally:
        conn.close()


_init_db()


def _normalize_query(text: str) -> str:
    normalized = (text or "").lower().strip()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _query_hash(normalized: str, model: str, profile: str) -> str:
    key = f"{normalized}|{model}|{profile}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def get_cached(query: str, model_name: str, profile_name: str) -> str | None:
    normalized = _normalize_query(query)
    if len(normalized) < 10:
        return None

    qhash = _query_hash(normalized, model_name, profile_name)
    now = time.time()

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, response, created_at FROM cache WHERE query_hash = ?",
            (qhash,),
        ).fetchone()
        if not row:
            return None

        if now - row["created_at"] > CACHE_TTL:
            conn.execute("DELETE FROM cache WHERE id = ?", (row["id"],))
            conn.commit()
            return None

        conn.execute(
            "UPDATE cache SET hit_count = hit_count + 1 WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        return row["response"]
    finally:
        conn.close()


def set_cached(query: str, model_name: str, profile_name: str, response: str) -> None:
    normalized = _normalize_query(query)
    if len(normalized) < 10 or len(response) < 20:
        return

    if response.startswith("⚠️") or "ошибка" in response.lower()[:50]:
        return

    qhash = _query_hash(normalized, model_name, profile_name)
    now = time.time()

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO cache (
                query_hash,
                query_normalized,
                model_name,
                profile_name,
                response,
                created_at,
                hit_count
            )
            VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(query_hash) DO UPDATE SET
                response = excluded.response,
                created_at = excluded.created_at,
                hit_count = 0
            """,
            (qhash, normalized, model_name, profile_name, response, now),
        )

        count = conn.execute("SELECT COUNT(*) AS cnt FROM cache").fetchone()["cnt"]
        if count > MAX_CACHE_SIZE:
            delete_count = MAX_CACHE_SIZE // 5
            conn.execute(
                """
                DELETE FROM cache WHERE id IN (
                    SELECT id FROM cache
                    ORDER BY hit_count ASC, created_at ASC
                    LIMIT ?
                )
                """,
                (delete_count,),
            )

        conn.commit()
    finally:
        conn.close()


def should_cache(query: str, route: str) -> bool:
    normalized = (query or "").lower()

    if any(
        word in normalized
        for word in ("запомни", "забудь", "сохрани в память", "удали из памяти")
    ):
        return False

    if route in ("project", "code"):
        return False

    temporal = detect_temporal_intent(query)
    if temporal.get("requires_web") or temporal.get("freshness_sensitive"):
        return False

    if any(
        word in normalized
        for word in ("сейчас", "сегодня", "прямо сейчас", "только что", "right now")
    ):
        return False

    return True


def clear_cache() -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM cache")
        conn.commit()
    finally:
        conn.close()


def cache_stats() -> dict:
    conn = _connect()
    try:
        total = conn.execute("SELECT COUNT(*) AS cnt FROM cache").fetchone()["cnt"]
        hits = conn.execute(
            "SELECT SUM(hit_count) AS total_hits FROM cache"
        ).fetchone()["total_hits"] or 0
        return {
            "total_entries": total,
            "total_hits": hits,
            "max_size": MAX_CACHE_SIZE,
            "ttl_seconds": CACHE_TTL,
        }
    finally:
        conn.close()
