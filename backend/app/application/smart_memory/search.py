from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from app.application.smart_memory.store import connect_memory_db, normalize_profile


TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ_-]+")

STOP_WORDS = {
    "и", "в", "на", "с", "по", "для", "не", "от", "за", "из", "к", "до",
    "что", "как", "это", "он", "она", "они", "мой", "моя", "мое", "мои", "мне",
    "ты", "вы", "я", "мы", "его", "ее", "их", "но", "а", "или", "то",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "to", "of", "in", "for", "on", "with",
    "at", "by", "from", "up", "about", "into", "through", "during", "before",
    "after", "and", "but", "or", "if", "then", "than", "that", "this",
}


def tokenize(text: str) -> list[str]:
    words = TOKEN_RE.findall((text or "").lower())
    return [word for word in words if word not in STOP_WORDS and len(word) > 1]


def similarity(left: str, right: str) -> float:
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


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
        params = (normalize_profile(profile_name),)

    sql += " ORDER BY importance DESC, updated_at DESC"

    conn = connect_memory_db()
    try:
        all_rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    if not all_rows:
        return {"ok": True, "items": [], "count": 0, "query": normalized_query}

    query_tokens = tokenize(normalized_query)
    if not query_tokens:
        return {"ok": True, "items": [], "count": 0, "query": normalized_query}

    doc_freq: Counter[str] = Counter()
    doc_tokens_list: list[set[str]] = []

    for row in all_rows:
        tokens = set(tokenize(row["text"]))
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
        conn = connect_memory_db()
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
