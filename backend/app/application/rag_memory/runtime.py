from __future__ import annotations

import json
import logging
import math
from typing import Any, Callable


logger = logging.getLogger(__name__)


def init_db(*, conn_factory: Callable[[], Any]) -> None:
    conn = conn_factory()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                category TEXT DEFAULT 'fact',
                embedding TEXT DEFAULT '',
                importance INTEGER DEFAULT 5,
                access_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_seed_data(*, conn_factory: Callable[[], Any], seed_rag_text: str) -> None:
    conn = conn_factory()
    try:
        conn.execute(
            "DELETE FROM rag_items WHERE LOWER(TRIM(text)) = ?",
            (seed_rag_text,),
        )
        conn.commit()
    finally:
        conn.close()


def get_embedding(*, embed_model: str, text: str) -> list[float] | None:
    try:
        import ollama

        response = ollama.embed(model=embed_model, input=text)
        embeddings = response.get("embeddings") or response.get("embedding")
        if embeddings:
            if isinstance(embeddings[0], list):
                return embeddings[0]
            return embeddings
        return None
    except Exception as exc:  # pragma: no cover - runtime dependency path
        logger.warning("Embedding failed: %s", exc)
        return None


def cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def add_to_rag(
    *,
    conn_factory: Callable[[], Any],
    get_embedding_func: Callable[[str], list[float] | None],
    text: str,
    category: str = "fact",
    importance: int = 5,
) -> dict[str, Any]:
    text = text.strip()
    if not text or len(text) < 3:
        return {"ok": False, "error": "Текст слишком короткий"}

    embedding = get_embedding_func(text)
    embedding_json = json.dumps(embedding) if embedding else ""

    conn = conn_factory()
    try:
        cursor = conn.execute(
            "INSERT INTO rag_items (text, category, embedding, importance) VALUES (?, ?, ?, ?)",
            (text, category, embedding_json, importance),
        )
        item_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "id": item_id, "has_embedding": bool(embedding)}


def search_rag(
    *,
    conn_factory: Callable[[], Any],
    get_embedding_func: Callable[[str], list[float] | None],
    cosine_sim_func: Callable[[list[float], list[float]], float],
    query: str,
    limit: int = 5,
    min_score: float = 0.3,
) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"ok": True, "items": [], "count": 0}

    query_embedding = get_embedding_func(query)

    conn = conn_factory()
    try:
        rows = conn.execute("SELECT * FROM rag_items ORDER BY importance DESC").fetchall()
    finally:
        conn.close()

    if not rows:
        return {"ok": True, "items": [], "count": 0}

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        row_dict = dict(row)
        score = 0.0

        if query_embedding and row_dict.get("embedding"):
            try:
                item_embedding = json.loads(row_dict["embedding"])
                score = cosine_sim_func(query_embedding, item_embedding)
            except (json.JSONDecodeError, TypeError):
                pass

        if score < 0.1:
            text_lower = row_dict["text"].lower()
            query_lower = query.lower()
            keywords = [word for word in query_lower.split() if len(word) > 2]
            if keywords:
                matches = sum(1 for keyword in keywords if keyword in text_lower)
                score = max(score, matches / len(keywords) * 0.5)

        score *= 1 + row_dict.get("importance", 5) / 20.0

        if score >= min_score:
            row_dict.pop("embedding", None)
            scored.append((score, row_dict))

    scored.sort(key=lambda item: -item[0])
    items = [{"score": round(score, 3), **item} for score, item in scored[:limit]]

    if items:
        conn = conn_factory()
        try:
            for item in items:
                conn.execute(
                    "UPDATE rag_items SET access_count = access_count + 1 WHERE id = ?",
                    (item["id"],),
                )
            conn.commit()
        finally:
            conn.close()

    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "method": "embedding" if query_embedding else "keyword",
    }


def get_rag_context(
    *,
    search_rag_func: Callable[..., dict[str, Any]],
    query: str,
    max_items: int = 5,
    max_chars: int = 2000,
) -> str:
    result = search_rag_func(query, limit=max_items)
    items = result.get("items", [])
    if not items:
        return ""

    lines: list[str] = []
    total = 0
    for item in items:
        line = f"- {item['text']}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)

    if not lines:
        return ""
    return "Context notes (use naturally, do not expose source):\n" + "\n".join(lines)


def list_rag(*, conn_factory: Callable[[], Any], limit: int = 50) -> dict[str, Any]:
    conn = conn_factory()
    try:
        rows = conn.execute(
            "SELECT id, text, category, importance, access_count, created_at FROM rag_items ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "items": [dict(row) for row in rows], "count": len(rows)}


def delete_rag(*, conn_factory: Callable[[], Any], item_id: int) -> dict[str, Any]:
    conn = conn_factory()
    try:
        conn.execute("DELETE FROM rag_items WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def rag_stats(*, conn_factory: Callable[[], Any], embed_model: str) -> dict[str, Any]:
    conn = conn_factory()
    try:
        total = conn.execute("SELECT COUNT(*) FROM rag_items").fetchone()[0]
        with_embeddings = conn.execute(
            "SELECT COUNT(*) FROM rag_items WHERE embedding != ''"
        ).fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "total": total, "with_embeddings": with_embeddings, "model": embed_model}

