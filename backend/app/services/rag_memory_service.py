"""
rag_memory.py — RAG память через Ollama embeddings.

Работает без FAISS — использует cosine similarity на numpy/чистом Python.
Embeddings через Ollama (модель nomic-embed-text или mxbai-embed-large).

API:
  add_to_rag(text, category) — добавляет запись + вектор
  search_rag(query, limit)   — семантический поиск
  get_rag_context(query)     — готовый контекст для LLM
"""
from __future__ import annotations
import json
import logging
import math
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path("data/rag_memory.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

EMBED_MODEL = "nomic-embed-text"  # Маленькая модель для embeddings
EMBED_DIM = 768  # Размерность nomic-embed-text


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _init():
    c = _conn()
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS rag_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                category TEXT DEFAULT 'fact',
                embedding TEXT DEFAULT '',
                importance INTEGER DEFAULT 5,
                access_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.commit()
    finally:
        c.close()

_init()


# ═══════════════════════════════════════════════════════════════
# EMBEDDINGS
# ═══════════════════════════════════════════════════════════════

def _get_embedding(text: str) -> list[float] | None:
    """Получает embedding через Ollama."""
    try:
        import ollama
        resp = ollama.embed(model=EMBED_MODEL, input=text)
        # Ollama возвращает embeddings в resp["embeddings"][0]
        embeddings = resp.get("embeddings") or resp.get("embedding")
        if embeddings:
            if isinstance(embeddings[0], list):
                return embeddings[0]
            return embeddings
        return None
    except Exception as e:
        logger.warning(f"Embedding failed: {e}")
        return None


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity без numpy."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ═══════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════

def add_to_rag(text: str, category: str = "fact", importance: int = 5) -> dict:
    """Добавляет запись с embedding."""
    text = text.strip()
    if not text or len(text) < 3:
        return {"ok": False, "error": "Текст слишком короткий"}

    embedding = _get_embedding(text)
    emb_json = json.dumps(embedding) if embedding else ""

    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO rag_items (text, category, embedding, importance) VALUES (?, ?, ?, ?)",
            (text, category, emb_json, importance)
        )
        item_id = cur.lastrowid
        c.commit()
    finally:
        c.close()

    return {"ok": True, "id": item_id, "has_embedding": bool(embedding)}


def search_rag(query: str, limit: int = 5, min_score: float = 0.3) -> dict:
    """Семантический поиск: embedding query → cosine similarity."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "items": [], "count": 0}

    query_emb = _get_embedding(query)

    c = _conn()
    try:
        rows = c.execute("SELECT * FROM rag_items ORDER BY importance DESC").fetchall()
    finally:
        c.close()

    if not rows:
        return {"ok": True, "items": [], "count": 0}

    scored = []
    for row in rows:
        row_dict = dict(row)
        score = 0.0

        if query_emb and row_dict.get("embedding"):
            try:
                item_emb = json.loads(row_dict["embedding"])
                score = _cosine_sim(query_emb, item_emb)
            except (json.JSONDecodeError, TypeError):
                pass

        # Fallback: keyword matching если нет embedding
        if score < 0.1:
            text_lower = row_dict["text"].lower()
            query_lower = query.lower()
            keywords = [w for w in query_lower.split() if len(w) > 2]
            if keywords:
                matches = sum(1 for k in keywords if k in text_lower)
                score = max(score, matches / len(keywords) * 0.5)

        # Бонус за importance
        score *= (1 + row_dict.get("importance", 5) / 20.0)

        if score >= min_score:
            row_dict.pop("embedding", None)  # Не возвращаем вектор
            scored.append((score, row_dict))

    scored.sort(key=lambda x: -x[0])
    items = [{"score": round(s, 3), **item} for s, item in scored[:limit]]

    # Обновляем access_count
    if items:
        c = _conn()
        try:
            for item in items:
                c.execute("UPDATE rag_items SET access_count = access_count + 1 WHERE id = ?", (item["id"],))
            c.commit()
        finally:
            c.close()

    return {"ok": True, "items": items, "count": len(items), "method": "embedding" if query_emb else "keyword"}


def get_rag_context(query: str, max_items: int = 5, max_chars: int = 2000) -> str:
    """Возвращает релевантные воспоминания для LLM промпта."""
    result = search_rag(query, limit=max_items)
    items = result.get("items", [])
    if not items:
        return ""

    lines = []
    total = 0
    for item in items:
        line = f"- [{item.get('category','fact')}] {item['text']}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)

    return "Из RAG-памяти Jarvis:\n" + "\n".join(lines) if lines else ""


def list_rag(limit: int = 50) -> dict:
    c = _conn()
    try:
        rows = c.execute("SELECT id, text, category, importance, access_count, created_at FROM rag_items ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    finally:
        c.close()
    return {"ok": True, "items": [dict(r) for r in rows], "count": len(rows)}


def delete_rag(item_id: int) -> dict:
    c = _conn()
    try:
        c.execute("DELETE FROM rag_items WHERE id = ?", (item_id,))
        c.commit()
    finally:
        c.close()
    return {"ok": True}


def rag_stats() -> dict:
    c = _conn()
    try:
        total = c.execute("SELECT COUNT(*) FROM rag_items").fetchone()[0]
        with_emb = c.execute("SELECT COUNT(*) FROM rag_items WHERE embedding != ''").fetchone()[0]
    finally:
        c.close()
    return {"ok": True, "total": total, "with_embeddings": with_emb, "model": EMBED_MODEL}
