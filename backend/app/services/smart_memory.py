from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from typing import Any

from app.core.data_files import sqlite_data_file


DEFAULT_PROFILE = "default"
DB_PATH = sqlite_data_file("smart_memory.db", key_tables=("memories",))
TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ_-]+")

_STOP_WORDS = {
    "и", "в", "на", "с", "по", "для", "не", "от", "за", "из", "к", "до",
    "что", "как", "это", "он", "она", "они", "мой", "моя", "мое", "мои", "мне",
    "ты", "вы", "я", "мы", "его", "ее", "их", "но", "а", "или", "то",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "to", "of", "in", "for", "on", "with",
    "at", "by", "from", "up", "about", "into", "through", "during", "before",
    "after", "and", "but", "or", "if", "then", "than", "that", "this",
}

_REMEMBER_PATTERNS = [
    r"запомни[,:]?\s+(?:что\s+)?(.+)",
    r"сохрани[,:]?\s+(?:что\s+)?(.+)",
    r"remember[,:]?\s+(?:that\s+)?(.+)",
    r"save[,:]?\s+(?:that\s+)?(.+)",
    r"мой (?:сервер|ip|адрес|номер|пароль|ключ|api).+",
    r"я (?:живу|работаю|учусь|люблю|предпочитаю|использую).+",
    r"меня зовут\s+.+",
    r"my name is\s+.+",
]

_FACT_PATTERNS = [
    (r"(?:мой|моя|мое|мои)\s+((?:сервер|ip|адрес|api|ключ|токен|email|почта|имя|название)\s*(?:—|:|-|это)?\s*.+)", "preference"),
    (r"(?:я\s+(?:живу|работаю|учусь|люблю|предпочитаю|использую))\s+(.+)", "preference"),
    (r"(?:меня зовут|my name is)\s+(.+)", "fact"),
    (r"(?:ip|сервер|server)\s*(?:—|:|-|=)\s*(\S+)", "fact"),
    (r"(?:api.?key|token|ключ)\s*(?:—|:|-|=)\s*(\S+)", "fact"),
]


def _normalize_profile(profile_name: str | None) -> str:
    profile = (profile_name or "").strip()
    return profile or DEFAULT_PROFILE


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_db() -> None:
    conn = _conn()
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


def _tokenize(text: str) -> list[str]:
    words = TOKEN_RE.findall((text or "").lower())
    return [word for word in words if word not in _STOP_WORDS and len(word) > 1]


def _similarity(left: str, right: str) -> float:
    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def add_memory(
    text: str,
    category: str = "fact",
    source: str = "auto",
    importance: int = 5,
    profile_name: str | None = None,
) -> dict[str, Any]:
    normalized_profile = _normalize_profile(profile_name)
    normalized_text = (text or "").strip()

    if len(normalized_text) < 3:
        return {"ok": False, "error": "Text is too short", "profile_name": normalized_profile}

    existing = search_memory(normalized_text, limit=3, profile_name=normalized_profile)
    for item in existing.get("items", []):
        if _similarity(normalized_text.lower(), item["text"].lower()) > 0.85:
            conn = _conn()
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

    conn = _conn()
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


def list_profiles() -> dict[str, Any]:
    conn = _conn()
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
        params.append(_normalize_profile(profile_name))
    if category:
        where_parts.append("category = ?")
        params.append(category)

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    conn = _conn()
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
        params.append(_normalize_profile(profile_name))

    conn = _conn()
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
        params = (_normalize_profile(profile_name),)

    conn = _conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        deleted = cur.rowcount or 0
    finally:
        conn.close()

    return {"ok": True, "deleted": deleted}


def search_memory(
    query: str,
    limit: int = 10,
    min_score: float = 0.1,
    profile_name: str | None = None,
) -> dict[str, Any]:
    normalized_query = (query or "").strip()
    if not normalized_query:
        return {"ok": True, "items": [], "count": 0}

    safe_limit = max(1, int(limit))
    params: tuple[Any, ...] = ()
    sql = "SELECT * FROM memories"

    if profile_name is not None:
        sql += " WHERE profile_name = ?"
        params = (_normalize_profile(profile_name),)

    sql += " ORDER BY importance DESC, updated_at DESC"

    conn = _conn()
    try:
        all_rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    if not all_rows:
        return {"ok": True, "items": [], "count": 0, "query": normalized_query}

    query_tokens = _tokenize(normalized_query)
    if not query_tokens:
        return {"ok": True, "items": [], "count": 0, "query": normalized_query}

    doc_freq: Counter[str] = Counter()
    doc_tokens_list: list[set[str]] = []

    for row in all_rows:
        tokens = set(_tokenize(row["text"]))
        doc_tokens_list.append(tokens)
        for token in tokens:
            doc_freq[token] += 1

    n_docs = len(all_rows)
    scored: list[tuple[float, dict[str, Any]]] = []

    for index, row in enumerate(all_rows):
        doc_tokens = doc_tokens_list[index]
        score = 0.0
        for query_token in query_tokens:
            if query_token in doc_tokens:
                idf = math.log(n_docs / (1 + doc_freq.get(query_token, 0)))
                score += 1 + idf

        score *= 1 + row["importance"] / 20.0
        if score >= min_score:
            scored.append((score, dict(row)))

    scored.sort(key=lambda item: item[0], reverse=True)
    items = [item for _, item in scored[:safe_limit]]

    if items:
        conn = _conn()
        try:
            for item in items:
                conn.execute(
                    "UPDATE memories SET access_count = access_count + 1 WHERE id = ?",
                    (item["id"],),
                )
            conn.commit()
        finally:
            conn.close()

    return {"ok": True, "items": items, "count": len(items), "query": normalized_query}


def is_memory_command(text: str) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    return bool(re.match(r"^(запомни|сохрани|remember|save)\b", normalized))


def _classify_memory_text(text: str) -> str:
    normalized = (text or "").strip().lower()
    if not normalized:
        return "fact"

    if re.search(r"\b(для |всегда |никогда |используй|не используй|отвечай|пиши|говори|remember to|always|never)\b", normalized):
        return "instruction"
    if re.search(r"\b(люблю|нравит|предпочита|хочу|нужно|важно|удобно|коротк|подробн|минимализм|новости)\b", normalized):
        return "preference"
    return "fact"


def extract_and_save(
    user_message: str,
    assistant_message: str = "",
    profile_name: str | None = None,
) -> list[dict[str, Any]]:
    del assistant_message

    normalized_profile = _normalize_profile(profile_name)
    normalized_text = (user_message or "").strip()
    if not normalized_text:
        return []

    saved: list[dict[str, Any]] = []

    for pattern in _REMEMBER_PATTERNS:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if not match:
            continue
        fact = match.group(1) if match.lastindex else match.group(0)
        fact = fact.strip().rstrip(".")
        if len(fact) > 5:
            result = add_memory(
                fact,
                category=_classify_memory_text(fact),
                source="user_command",
                importance=8,
                profile_name=normalized_profile,
            )
            if result.get("ok"):
                saved.append(result)
        return saved

    for pattern, category in _FACT_PATTERNS:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if not match:
            continue
        fact = match.group(1) if match.lastindex else match.group(0)
        fact = fact.strip().rstrip(".")
        if len(fact) > 3:
            result = add_memory(
                fact,
                category=category,
                source="auto_extract",
                importance=6,
                profile_name=normalized_profile,
            )
            if result.get("ok"):
                saved.append(result)

    return saved


def get_relevant_context(
    query: str,
    max_items: int = 5,
    max_chars: int = 1500,
    profile_name: str | None = None,
) -> str:
    result = search_memory(query, limit=max_items, profile_name=profile_name)
    items = result.get("items", [])
    if not items:
        return ""

    lines: list[str] = []
    total_chars = 0

    for item in items:
        line = f"- {item['text']}"
        if total_chars + len(line) > max_chars:
            break
        lines.append(line)
        total_chars += len(line)

    if not lines:
        return ""

    return "Context notes (do not mention memory or source unless asked):\n" + "\n".join(lines)


def get_stats(profile_name: str | None = None) -> dict[str, Any]:
    params: tuple[Any, ...] = ()
    where_sql = ""

    if profile_name is not None:
        where_sql = "WHERE profile_name = ?"
        params = (_normalize_profile(profile_name),)

    conn = _conn()
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
        payload["profile_name"] = _normalize_profile(profile_name)
    return payload
