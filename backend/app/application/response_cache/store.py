from __future__ import annotations

import time
from typing import Any, Callable


def init_db(*, connect_func: Callable[[], Any]) -> None:
    conn = connect_func()
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


def get_cached(
    *,
    connect_func: Callable[[], Any],
    normalize_query_func: Callable[[str], str],
    query_hash_func: Callable[[str, str, str], str],
    cache_ttl: int,
    query: str,
    model_name: str,
    profile_name: str,
) -> str | None:
    normalized = normalize_query_func(query)
    if len(normalized) < 10:
        return None

    qhash = query_hash_func(normalized, model_name, profile_name)
    now = time.time()

    conn = connect_func()
    try:
        row = conn.execute(
            "SELECT id, response, created_at FROM cache WHERE query_hash = ?",
            (qhash,),
        ).fetchone()
        if not row:
            return None

        if now - row["created_at"] > cache_ttl:
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


def set_cached(
    *,
    connect_func: Callable[[], Any],
    normalize_query_func: Callable[[str], str],
    query_hash_func: Callable[[str, str, str], str],
    max_cache_size: int,
    query: str,
    model_name: str,
    profile_name: str,
    response: str,
) -> None:
    normalized = normalize_query_func(query)
    if len(normalized) < 10 or len(response) < 20:
        return

    if response.startswith("⚠️") or "ошибка" in response.lower()[:50]:
        return

    qhash = query_hash_func(normalized, model_name, profile_name)
    now = time.time()

    conn = connect_func()
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
        if count > max_cache_size:
            delete_count = max_cache_size // 5
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


def clear_cache(*, connect_func: Callable[[], Any]) -> None:
    conn = connect_func()
    try:
        conn.execute("DELETE FROM cache")
        conn.commit()
    finally:
        conn.close()


def cache_stats(
    *,
    connect_func: Callable[[], Any],
    max_cache_size: int,
    cache_ttl: int,
) -> dict[str, int]:
    conn = connect_func()
    try:
        total = conn.execute("SELECT COUNT(*) AS cnt FROM cache").fetchone()["cnt"]
        hits = conn.execute("SELECT SUM(hit_count) AS total_hits FROM cache").fetchone()["total_hits"] or 0
        return {
            "total_entries": total,
            "total_hits": hits,
            "max_size": max_cache_size,
            "ttl_seconds": cache_ttl,
        }
    finally:
        conn.close()
