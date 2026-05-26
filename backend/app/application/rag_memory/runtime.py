from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import Any, Callable


logger = logging.getLogger(__name__)


def _text_hash(text: str) -> str:
    """Stable content hash used for dedup. SHA1 (12 hex chars) gives
    ~256B collision rate at our scale and keeps the column compact."""
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]


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
        # Schema upgrades — ALTER if missing. Older DBs created before
        # the rag_memory v2 changes will have just the original columns.
        existing_cols = {
            row[1]  # row = (cid, name, type, notnull, dflt, pk)
            for row in conn.execute("PRAGMA table_info(rag_items)").fetchall()
        }
        if "text_hash" not in existing_cols:
            conn.execute("ALTER TABLE rag_items ADD COLUMN text_hash TEXT DEFAULT ''")
            # Backfill hashes for any existing rows so dedup works on
            # them too.
            for row in conn.execute("SELECT id, text FROM rag_items WHERE COALESCE(text_hash, '') = ''").fetchall():
                conn.execute(
                    "UPDATE rag_items SET text_hash = ? WHERE id = ?",
                    (_text_hash(row[1] or ""), row[0]),
                )
        if "project" not in existing_cols:
            conn.execute("ALTER TABLE rag_items ADD COLUMN project TEXT DEFAULT ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rag_category ON rag_items(category)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rag_hash_cat ON rag_items(text_hash, category)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rag_project ON rag_items(project)"
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
    project: str = "",
) -> dict[str, Any]:
    text = text.strip()
    if not text or len(text) < 3:
        return {"ok": False, "error": "Текст слишком короткий"}

    text_hash = _text_hash(text)

    # Dedup: if a row with the same text + category exists, just bump
    # its importance (capped at 10) instead of inserting a duplicate.
    # This is the fix for `_try_remember_turn` flooding RAG with near-
    # identical agent_turn summaries.
    conn = conn_factory()
    try:
        existing = conn.execute(
            "SELECT id, importance FROM rag_items WHERE text_hash = ? AND category = ? LIMIT 1",
            (text_hash, category),
        ).fetchone()
        if existing:
            existing_id = existing[0] if not hasattr(existing, "keys") else existing["id"]
            existing_imp = existing[1] if not hasattr(existing, "keys") else existing["importance"]
            new_imp = min(10, int(existing_imp or 0) + 1)
            conn.execute(
                "UPDATE rag_items SET importance = ? WHERE id = ?",
                (new_imp, existing_id),
            )
            conn.commit()
            return {
                "ok": True,
                "id": existing_id,
                "action": "deduped",
                "importance": new_imp,
                "has_embedding": True,  # the existing row already has one (or doesn't — irrelevant)
            }
    finally:
        conn.close()

    embedding = get_embedding_func(text)
    embedding_json = json.dumps(embedding) if embedding else ""

    conn = conn_factory()
    try:
        cursor = conn.execute(
            """
            INSERT INTO rag_items (text, text_hash, category, embedding, importance, project)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (text, text_hash, category, embedding_json, importance, project or ""),
        )
        item_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "id": item_id, "action": "created", "has_embedding": bool(embedding)}


def _batch_cosine(query_vec: list[float], embeddings_matrix: Any) -> Any:
    """Bulk cosine similarity: query vs all rows in one numpy operation.

    Returns a numpy array of scores. Falls back to pure-Python pairwise
    cosine if numpy is unavailable (so the module still imports on
    minimal installs). Pure-Python path is much slower but functionally
    identical.
    """
    try:
        import numpy as np

        q = np.asarray(query_vec, dtype=np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return np.zeros(len(embeddings_matrix), dtype=np.float32)
        # embeddings_matrix is already a (N, dim) numpy array
        m = embeddings_matrix
        m_norms = np.linalg.norm(m, axis=1)
        m_norms[m_norms == 0.0] = 1.0  # avoid /0 — score stays 0 from dot
        dots = m @ q
        return dots / (m_norms * q_norm)
    except Exception:
        # Fallback: identical math without numpy
        scores = []
        for row in embeddings_matrix:
            scores.append(cosine_sim(query_vec, list(row)))
        return scores


def search_rag(
    *,
    conn_factory: Callable[[], Any],
    get_embedding_func: Callable[[str], list[float] | None],
    cosine_sim_func: Callable[[list[float], list[float]], float],
    query: str,
    limit: int = 5,
    min_score: float = 0.3,
    project: str | None = None,
) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"ok": True, "items": [], "count": 0}

    query_embedding = get_embedding_func(query)

    conn = conn_factory()
    try:
        if project:
            # Project-scope: items tagged with this project + globals
            # (project == '') so user-level facts still surface.
            rows = conn.execute(
                """
                SELECT * FROM rag_items
                WHERE project = ? OR project = '' OR project IS NULL
                ORDER BY importance DESC
                """,
                (project,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM rag_items ORDER BY importance DESC"
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"ok": True, "items": [], "count": 0}

    # ── Bulk-parse embeddings into a single numpy matrix once. This
    #    replaces the per-row `json.loads + python cosine` loop that
    #    was the main perf bottleneck (~300ms at 5K rows → ~5ms).
    parsed_rows: list[dict[str, Any]] = []
    embedding_list: list[list[float]] = []
    have_embedding: list[bool] = []
    for row in rows:
        row_dict = dict(row)
        parsed_rows.append(row_dict)
        raw_embed = row_dict.get("embedding") or ""
        if query_embedding and raw_embed:
            try:
                vec = json.loads(raw_embed)
                if isinstance(vec, list) and vec:
                    embedding_list.append(vec)
                    have_embedding.append(True)
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
        embedding_list.append([])
        have_embedding.append(False)

    # Vectorized cosine for rows that have an embedding.
    cosine_scores: list[float] = [0.0] * len(parsed_rows)
    if query_embedding and any(have_embedding):
        try:
            import numpy as np

            # Stack only the rows that have embeddings; remember their
            # positions so we can map scores back.
            dim = None
            with_idx: list[int] = []
            with_vecs: list[list[float]] = []
            for i, vec in enumerate(embedding_list):
                if not have_embedding[i]:
                    continue
                if dim is None:
                    dim = len(vec)
                if len(vec) != dim:
                    # Bad row (mismatched dim) — skip from vector path,
                    # will fall through to keyword fallback below.
                    have_embedding[i] = False
                    continue
                with_idx.append(i)
                with_vecs.append(vec)
            if with_vecs:
                matrix = np.asarray(with_vecs, dtype=np.float32)
                scores = _batch_cosine(query_embedding, matrix)
                for pos, idx in enumerate(with_idx):
                    cosine_scores[idx] = float(scores[pos])
        except Exception:
            # Last-resort fallback: pairwise via the injected
            # cosine_sim_func — same behavior as old code, slower.
            for i, vec in enumerate(embedding_list):
                if have_embedding[i]:
                    cosine_scores[i] = cosine_sim_func(query_embedding, vec)

    # Score, optionally fall back to keyword overlap, apply importance
    # boost, then filter by min_score.
    keywords = [word for word in query.lower().split() if len(word) > 2]
    scored: list[tuple[float, dict[str, Any]]] = []
    for i, row_dict in enumerate(parsed_rows):
        score = cosine_scores[i]
        if score < 0.1 and keywords:
            text_lower = (row_dict.get("text") or "").lower()
            matches = sum(1 for keyword in keywords if keyword in text_lower)
            score = max(score, matches / len(keywords) * 0.5)
        score *= 1 + (row_dict.get("importance", 5) or 5) / 20.0
        if score >= min_score:
            row_dict.pop("embedding", None)  # don't leak vector to caller
            row_dict.pop("text_hash", None)  # internal
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

