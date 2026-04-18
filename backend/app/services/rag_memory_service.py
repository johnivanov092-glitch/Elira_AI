"""RAG memory backed by SQLite and Ollama embeddings."""

from __future__ import annotations

import json
import logging
import math
import sqlite3

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

logger = logging.getLogger(__name__)

DB_PATH = sqlite_data_file("rag_memory.db", key_tables=("rag_items",))
SEED_RAG_TEXT = "rag alpha memory"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def _init() -> None:
    conn = _conn()
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


def _cleanup_seed_data() -> None:
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM rag_items WHERE LOWER(TRIM(text)) = ?",
            (SEED_RAG_TEXT,),
        )
        conn.commit()
    finally:
        conn.close()


_init()
_cleanup_seed_data()


def _get_embedding(text: str) -> list[float] | None:
    """Fetch an embedding from Ollama when available."""
    try:
        import ollama

        response = ollama.embed(model=EMBED_MODEL, input=text)
        embeddings = response.get("embeddings") or response.get("embedding")
        if embeddings:
            if isinstance(embeddings[0], list):
                return embeddings[0]
            return embeddings
        return None
    except Exception as exc:  # pragma: no cover - runtime dependency path
        logger.warning("Embedding failed: %s", exc)
        return None


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity without numpy."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def add_to_rag(text: str, category: str = "fact", importance: int = 5) -> dict:
    """Store a new RAG fact and embedding."""
    text = text.strip()
    if not text or len(text) < 3:
        return {"ok": False, "error": "Текст слишком короткий"}

    embedding = _get_embedding(text)
    embedding_json = json.dumps(embedding) if embedding else ""

    conn = _conn()
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


def search_rag(query: str, limit: int = 5, min_score: float = 0.3) -> dict:
    """Run semantic or keyword fallback search over RAG memory."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "items": [], "count": 0}

    query_embedding = _get_embedding(query)

    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM rag_items ORDER BY importance DESC").fetchall()
    finally:
        conn.close()

    if not rows:
        return {"ok": True, "items": [], "count": 0}

    scored: list[tuple[float, dict]] = []
    for row in rows:
        row_dict = dict(row)
        score = 0.0

        if query_embedding and row_dict.get("embedding"):
            try:
                item_embedding = json.loads(row_dict["embedding"])
                score = _cosine_sim(query_embedding, item_embedding)
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
        conn = _conn()
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


def get_rag_context(query: str, max_items: int = 5, max_chars: int = 2000) -> str:
    """Build internal prompt context without leaking raw service tags to users."""
    result = search_rag(query, limit=max_items)
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


def list_rag(limit: int = 50) -> dict:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, text, category, importance, access_count, created_at FROM rag_items ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "items": [dict(row) for row in rows], "count": len(rows)}


def delete_rag(item_id: int) -> dict:
    conn = _conn()
    try:
        conn.execute("DELETE FROM rag_items WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def rag_stats() -> dict:
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM rag_items").fetchone()[0]
        with_embeddings = conn.execute(
            "SELECT COUNT(*) FROM rag_items WHERE embedding != ''"
        ).fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "total": total, "with_embeddings": with_embeddings, "model": EMBED_MODEL}
