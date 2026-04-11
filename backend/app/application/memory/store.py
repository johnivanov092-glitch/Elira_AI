from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable


TimestampFactory = Callable[[], str]
ContentHashFunc = Callable[[str], str]
LoadMemoriesFunc = Callable[..., list[Any]]
AddMemoryFunc = Callable[..., bool]


def list_mem_profiles(*, db_path: str) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, emoji, created_at FROM mem_profiles ORDER BY id ASC"
        ).fetchall()
    return [{"id": row[0], "name": row[1], "emoji": row[2], "created_at": row[3]} for row in rows]


def create_mem_profile(
    *,
    db_path: str,
    name: str,
    emoji: str = "👤",
    now_iso_func: TimestampFactory,
) -> bool:
    normalized_name = name.strip()
    if not normalized_name or len(normalized_name) > 40:
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO mem_profiles (name, emoji, created_at) VALUES (?, ?, ?)",
                (normalized_name, emoji, now_iso_func()),
            )
            conn.commit()
        return True
    except Exception:
        return False


def delete_mem_profile(*, db_path: str, name: str) -> None:
    if name == "default":
        return
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM mem_profiles WHERE name = ?", (name,))
        conn.execute("DELETE FROM memories WHERE profile_name = ?", (name,))
        conn.commit()


def add_memory(
    *,
    db_path: str,
    content_hash_func: ContentHashFunc,
    now_iso_func: TimestampFactory,
    content: str,
    source: str = "manual",
    pinned: bool = False,
    memory_type: str = "general",
    profile_name: str = "",
    deduplicate: bool = True,
) -> bool:
    normalized_content = (content or "").strip()
    if not normalized_content:
        return False

    content_hash = content_hash_func(normalized_content)
    with sqlite3.connect(db_path) as conn:
        if deduplicate:
            existing = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ? AND profile_name = ? LIMIT 1",
                (content_hash, profile_name),
            ).fetchone()
            if existing:
                return False

        conn.execute(
            "INSERT INTO memories (content, source, created_at, pinned, memory_type, profile_name, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                normalized_content,
                source,
                now_iso_func(),
                int(pinned),
                memory_type,
                profile_name,
                content_hash,
            ),
        )
        conn.commit()
    return True


def load_memories(
    *,
    db_path: str,
    limit: int = 500,
    only_pinned: bool = False,
    profile_name: str = "",
) -> list[Any]:
    with sqlite3.connect(db_path) as conn:
        sql = "SELECT id, content, source, created_at, pinned, memory_type, profile_name FROM memories"
        clauses: list[str] = []
        params: list[Any] = []
        if only_pinned:
            clauses.append("pinned = 1")
        if profile_name:
            clauses.append("profile_name = ?")
            params.append(profile_name)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return conn.execute(sql, tuple(params)).fetchall()


def delete_memory(*, db_path: str, memory_id: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()


def clear_memories(*, db_path: str, profile_name: str = "") -> None:
    with sqlite3.connect(db_path) as conn:
        if profile_name:
            conn.execute("DELETE FROM memories WHERE profile_name = ?", (profile_name,))
        else:
            conn.execute("DELETE FROM memories")
        conn.commit()


def set_memory_pin(*, db_path: str, memory_id: int, pinned: bool) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE memories SET pinned = ? WHERE id = ?", (int(pinned), memory_id))
        conn.commit()


def export_memories(
    *,
    profile_name: str = "",
    load_memories_func: LoadMemoriesFunc,
) -> str:
    rows = load_memories_func(5000, profile_name=profile_name)
    payload = [
        {
            "id": row_id,
            "content": content,
            "source": source,
            "created_at": created_at,
            "pinned": pinned,
            "memory_type": memory_type,
            "profile_name": profile,
        }
        for row_id, content, source, created_at, pinned, memory_type, profile in rows
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def import_memories_from_json(
    *,
    text: str,
    add_memory_func: AddMemoryFunc,
    max_items: int = 2000,
) -> None:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("JSON должен быть списком объектов")
    if len(data) > max_items:
        raise ValueError(f"Слишком много записей: {len(data)} > {max_items}")

    for item in data:
        content = str(item.get("content", "")).strip()[:10000]
        if not content:
            continue
        add_memory_func(
            content=content,
            source=str(item.get("source", "import"))[:64],
            pinned=bool(item.get("pinned", False)),
            memory_type=str(item.get("memory_type", "general"))[:32],
            profile_name=str(item.get("profile_name", ""))[:64],
        )
