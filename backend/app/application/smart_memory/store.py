from __future__ import annotations

import sqlite3
from typing import Any

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite


DEFAULT_PROFILE = "default"
DB_PATH = sqlite_data_file("smart_memory.db", key_tables=("memories",))


def normalize_profile(profile_name: str | None) -> str:
    profile = (profile_name or "").strip()
    return profile or DEFAULT_PROFILE


def connect_memory_db() -> sqlite3.Connection:
    # WAL (connect_sqlite default) — a memory write must not block concurrent reads.
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row)


def init_memory_db() -> None:
    conn = connect_memory_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT NOT NULL DEFAULT 'default',
                text TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'fact',
                source TEXT NOT NULL DEFAULT 'auto',
                importance INTEGER NOT NULL DEFAULT 5,
                access_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "profile_name" not in columns:
            conn.execute(
                "ALTER TABLE memories ADD COLUMN profile_name TEXT NOT NULL DEFAULT 'default'"
            )
            conn.execute(
                "UPDATE memories SET profile_name = ? WHERE profile_name IS NULL OR TRIM(profile_name) = ''",
                (DEFAULT_PROFILE,),
            )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem_profile_cat ON memories(profile_name, category)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem_profile_imp ON memories(profile_name, importance DESC, updated_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


init_memory_db()


def add_memory(
    text: str,
    category: str = "fact",
    source: str = "auto",
    importance: int = 5,
    profile_name: str | None = None,
    replaces_id: int | str | None = None,
) -> dict[str, Any]:
    from app.application.smart_memory.search import search_memory, similarity, tokenize

    normalized_profile = normalize_profile(profile_name)
    normalized_text = (text or "").strip()

    if len(normalized_text) < 3:
        return {"ok": False, "error": "Text is too short", "profile_name": normalized_profile}

    existing = search_memory(normalized_text, limit=50, profile_name=normalized_profile)
    is_correction = source == "user_correction" or replaces_id is not None
    correction_source = "user_correction" if replaces_id is not None else source
    if is_correction:
        target: dict[str, Any] | None = None
        if replaces_id is not None:
            try:
                target_id = int(replaces_id)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "Replacement memory id is invalid",
                    "profile_name": normalized_profile,
                }
            conn = connect_memory_db()
            try:
                row = conn.execute(
                    "SELECT * FROM memories WHERE id = ? AND profile_name = ?",
                    (target_id, normalized_profile),
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                return {
                    "ok": False,
                    "error": "Replacement memory was not found in this profile",
                    "profile_name": normalized_profile,
                }
            target = dict(row)
        else:
            normalized_tokens = set(tokenize(normalized_text))
            candidates: list[tuple[float, dict[str, Any]]] = []
            for item in existing.get("items", []):
                previous_text = str(item.get("text") or "")
                score = similarity(normalized_text.lower(), previous_text.lower())
                shared_tokens = normalized_tokens.intersection(tokenize(previous_text))
                exact = normalized_text.casefold() == previous_text.strip().casefold()
                if exact or (score >= 0.45 and len(shared_tokens) >= 2):
                    candidates.append((score, item))
            if candidates:
                _score, target = max(
                    candidates,
                    key=lambda pair: (pair[0], int(pair[1].get("importance") or 0)),
                )
        if target is not None:
            previous_text = str(target.get("text") or "")
            conn = connect_memory_db()
            try:
                conn.execute(
                    """
                    UPDATE memories
                    SET text = ?, category = ?, source = ?, importance = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND profile_name = ?
                    """,
                    (
                        normalized_text,
                        category,
                        correction_source,
                        max(1, min(int(importance), 10)),
                        target["id"],
                        normalized_profile,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            return {
                "ok": True,
                "action": "corrected",
                "id": target["id"],
                "text": normalized_text,
                "previous_text": previous_text,
                "category": category,
                "profile_name": normalized_profile,
            }

    for item in existing.get("items", []):
        if similarity(normalized_text.lower(), item["text"].lower()) > 0.85:
            conn = connect_memory_db()
            try:
                conn.execute(
                    """
                    UPDATE memories
                    SET importance = MIN(importance + 1, 10),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND profile_name = ?
                    """,
                    (item["id"], normalized_profile),
                )
                conn.commit()
            finally:
                conn.close()
            return {
                "ok": True,
                "action": "updated",
                "id": item["id"],
                "text": normalized_text,
                "category": item["category"],
                "profile_name": normalized_profile,
            }

    conn = connect_memory_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO memories (profile_name, text, category, source, importance)
            VALUES (?, ?, ?, ?, ?)
            """,
            (normalized_profile, normalized_text, category, source, int(importance)),
        )
        mem_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "action": "created",
        "id": mem_id,
        "text": normalized_text,
        "category": category,
        "profile_name": normalized_profile,
    }


def prune_volatile_memories(
    *,
    max_age_days: int = 7,
    dry_run: bool = True,
    profile_name: str | None = None,
) -> dict[str, Any]:
    """Remove only aged operational-state rows; durable user facts are untouched."""
    safe_days = max(1, int(max_age_days))
    where = ["category = 'volatile_fact'", "updated_at < datetime('now', ?)"]
    params: list[Any] = [f"-{safe_days} days"]
    if profile_name is not None:
        where.append("profile_name = ?")
        params.append(normalize_profile(profile_name))
    where_sql = " AND ".join(where)

    conn = connect_memory_db()
    try:
        candidates = int(
            conn.execute(f"SELECT COUNT(*) FROM memories WHERE {where_sql}", params).fetchone()[0]
        )
        sample = [
            {"id": int(row["id"]), "text": str(row["text"] or "")[:160]}
            for row in conn.execute(
                f"SELECT id, text FROM memories WHERE {where_sql} ORDER BY updated_at LIMIT 5",
                params,
            ).fetchall()
        ]
        pruned = 0
        if not dry_run and candidates:
            cursor = conn.execute(f"DELETE FROM memories WHERE {where_sql}", params)
            pruned = int(cursor.rowcount or 0)
            conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "candidates": candidates,
        "pruned": pruned,
        "dry_run": bool(dry_run),
        "max_age_days": safe_days,
        "profile_name": normalize_profile(profile_name) if profile_name is not None else None,
        "sample": sample,
    }


def list_profiles() -> dict[str, Any]:
    conn = connect_memory_db()
    try:
        rows = conn.execute(
            """
            SELECT profile_name, COUNT(*) AS item_count
            FROM memories
            GROUP BY profile_name
            ORDER BY profile_name
            """
        ).fetchall()
    finally:
        conn.close()

    profiles = [
        {"name": row["profile_name"], "count": row["item_count"]}
        for row in rows
    ]
    return {"ok": True, "profiles": profiles, "count": len(profiles)}


def list_memories(
    category: str | None = None,
    limit: int = 50,
    profile_name: str | None = None,
) -> dict[str, Any]:
    safe_limit = max(1, int(limit))
    params: list[Any] = []
    where_parts: list[str] = []

    if profile_name is not None:
        where_parts.append("profile_name = ?")
        params.append(normalize_profile(profile_name))
    if category:
        where_parts.append("category = ?")
        params.append(category)

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    conn = connect_memory_db()
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM memories
            {where_sql}
            ORDER BY importance DESC, updated_at DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()
    finally:
        conn.close()

    return {"ok": True, "items": [dict(row) for row in rows], "count": len(rows)}


def delete_memory(mem_id: int, profile_name: str | None = None) -> dict[str, Any]:
    params: list[Any] = [int(mem_id)]
    sql = "DELETE FROM memories WHERE id = ?"

    if profile_name is not None:
        sql += " AND profile_name = ?"
        params.append(normalize_profile(profile_name))

    conn = connect_memory_db()
    try:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        deleted = cur.rowcount or 0
    finally:
        conn.close()

    return {"ok": deleted > 0, "deleted_id": int(mem_id), "deleted": deleted}


def clear_all_memories(profile_name: str | None = None) -> dict[str, Any]:
    params: tuple[Any, ...] = ()
    sql = "DELETE FROM memories"

    if profile_name is not None:
        sql += " WHERE profile_name = ?"
        params = (normalize_profile(profile_name),)

    conn = connect_memory_db()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        deleted = cur.rowcount or 0
    finally:
        conn.close()

    return {"ok": True, "deleted": deleted}


def get_stats(profile_name: str | None = None) -> dict[str, Any]:
    params: tuple[Any, ...] = ()
    where_sql = ""

    if profile_name is not None:
        where_sql = "WHERE profile_name = ?"
        params = (normalize_profile(profile_name),)

    conn = connect_memory_db()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM memories {where_sql}",
            params,
        ).fetchone()[0]
        by_category = conn.execute(
            f"""
            SELECT category, COUNT(*) AS item_count
            FROM memories
            {where_sql}
            GROUP BY category
            """,
            params,
        ).fetchall()
        by_source = conn.execute(
            f"""
            SELECT source, COUNT(*) AS item_count
            FROM memories
            {where_sql}
            GROUP BY source
            """,
            params,
        ).fetchall()
        by_profile = conn.execute(
            """
            SELECT profile_name, COUNT(*) AS item_count
            FROM memories
            GROUP BY profile_name
            ORDER BY profile_name
            """
        ).fetchall()
    finally:
        conn.close()

    payload: dict[str, Any] = {
        "ok": True,
        "total": total,
        "by_category": {row["category"]: row["item_count"] for row in by_category},
        "by_source": {row["source"]: row["item_count"] for row in by_source},
    }
    if profile_name is None:
        payload["by_profile"] = {
            row["profile_name"]: row["item_count"]
            for row in by_profile
        }
    else:
        payload["profile_name"] = normalize_profile(profile_name)
    return payload
