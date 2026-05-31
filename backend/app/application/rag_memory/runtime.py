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


def _embedding_to_blob(vec: list[float] | None) -> bytes | None:
    """Pack an embedding into a compact float32 byte buffer.

    768-dim float32 = 3072 bytes per embedding. ~5x smaller than the
    JSON-text representation (~15KB per embedding) and ~50x faster to
    read back via numpy.frombuffer than json.loads.
    """
    if not vec:
        return None
    try:
        import numpy as np

        return np.asarray(vec, dtype=np.float32).tobytes()
    except Exception:
        # Numpy missing — fall back to a manual pack so reads still work
        import struct

        return struct.pack(f"{len(vec)}f", *(float(x) for x in vec))


def _blob_to_array(blob: bytes | None) -> Any:
    """Inverse of _embedding_to_blob. Returns a numpy array on the fast
    path, or a plain list as a fallback."""
    if not blob:
        return None
    try:
        import numpy as np

        return np.frombuffer(blob, dtype=np.float32)
    except Exception:
        import struct

        count = len(blob) // 4
        return list(struct.unpack(f"{count}f", blob))


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
        if "embedding_blob" not in existing_cols:
            conn.execute("ALTER TABLE rag_items ADD COLUMN embedding_blob BLOB DEFAULT NULL")
            # One-shot migration: convert any pre-existing JSON-text
            # embeddings into the new BLOB column. Done once, in batch,
            # so the perf win shows up immediately on first startup
            # after this code lands.
            rows_to_migrate = conn.execute(
                """
                SELECT id, embedding FROM rag_items
                WHERE COALESCE(embedding, '') != '' AND embedding_blob IS NULL
                """
            ).fetchall()
            migrated = 0
            for row in rows_to_migrate:
                row_id = row[0]
                raw = row[1]
                try:
                    vec = json.loads(raw)
                    if isinstance(vec, list) and vec:
                        blob = _embedding_to_blob(vec)
                        if blob is not None:
                            conn.execute(
                                "UPDATE rag_items SET embedding_blob = ? WHERE id = ?",
                                (blob, row_id),
                            )
                            migrated += 1
                except (json.JSONDecodeError, TypeError):
                    pass
            if migrated:
                logger.info("rag_memory: migrated %d embeddings from JSON to BLOB", migrated)
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
    # New rows store embeddings as compact float32 BLOB (3KB vs ~15KB
    # for JSON). The TEXT `embedding` column stays empty — kept for
    # back-compat with code that may still read it, but no new data
    # goes there.
    embedding_blob = _embedding_to_blob(embedding) if embedding else None

    conn = conn_factory()
    try:
        cursor = conn.execute(
            """
            INSERT INTO rag_items (text, text_hash, category, embedding, embedding_blob, importance, project)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (text, text_hash, category, "", embedding_blob, importance, project or ""),
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

    # Explicit column list — never SELECT *. Pulling the legacy
    # `embedding` TEXT column on every search wasted ~150ms at 2K rows
    # because each row carried ~12KB of stale JSON we no longer use
    # (BLOB is the source of truth now). We only fetch it when the BLOB
    # is missing, via a separate query, to drive lazy migration.
    base_cols = (
        "id, text, category, importance, access_count, created_at, "
        "embedding_blob, text_hash, project"
    )
    conn = conn_factory()
    try:
        if project:
            # Project-scope: items tagged with this project + globals
            # (project == '') so user-level facts still surface.
            rows = conn.execute(
                f"""
                SELECT {base_cols} FROM rag_items
                WHERE project = ? OR project = '' OR project IS NULL
                ORDER BY importance DESC
                """,
                (project,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {base_cols} FROM rag_items ORDER BY importance DESC"
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"ok": True, "items": [], "count": 0}

    # ── Bulk-parse embeddings into a single numpy matrix once.
    # Fast path: read BLOB column → numpy.frombuffer (microseconds per row).
    # Slow path (legacy data with no BLOB): a single follow-up query
    # fetches JSON for all blob-less rows, then we lazy-migrate them
    # to BLOB so subsequent searches hit the fast path.
    parsed_rows: list[dict[str, Any]] = []
    raw_vecs: list[Any] = []   # numpy arrays or lists, one per row (or None)
    have_embedding: list[bool] = []
    lazy_migrations: list[tuple[int, bytes]] = []
    missing_blob_ids: list[int] = []
    missing_blob_idx: dict[int, int] = {}  # id -> position in parsed_rows
    for row in rows:
        row_dict = dict(row)
        idx = len(parsed_rows)
        parsed_rows.append(row_dict)
        raw_vecs.append(None)
        have_embedding.append(False)
        if not query_embedding:
            continue
        blob = row_dict.get("embedding_blob")
        if blob:
            vec = _blob_to_array(blob)
            if vec is not None and hasattr(vec, "__len__") and len(vec) > 0:
                raw_vecs[idx] = vec
                have_embedding[idx] = True
        else:
            # No BLOB — defer to single batched JSON fetch below
            rid = int(row_dict.get("id", 0) or 0)
            if rid:
                missing_blob_ids.append(rid)
                missing_blob_idx[rid] = idx

    # Bulk-fetch JSON embeddings for rows without BLOB (legacy data).
    if missing_blob_ids and query_embedding:
        try:
            conn = conn_factory()
            try:
                placeholders = ",".join("?" for _ in missing_blob_ids)
                json_rows = conn.execute(
                    f"SELECT id, embedding FROM rag_items WHERE id IN ({placeholders})",
                    missing_blob_ids,
                ).fetchall()
            finally:
                conn.close()
            for jrow in json_rows:
                rid = jrow[0] if not hasattr(jrow, "keys") else jrow["id"]
                raw_text = jrow[1] if not hasattr(jrow, "keys") else jrow["embedding"]
                if not raw_text:
                    continue
                try:
                    parsed = json.loads(raw_text)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not (isinstance(parsed, list) and parsed):
                    continue
                idx = missing_blob_idx.get(rid)
                if idx is None:
                    continue
                raw_vecs[idx] = parsed
                have_embedding[idx] = True
                # Schedule write-back so next search is fast
                blob_for_migrate = _embedding_to_blob(parsed)
                if blob_for_migrate is not None:
                    lazy_migrations.append((rid, blob_for_migrate))
        except Exception:
            logger.debug("rag_memory: bulk JSON fetch for legacy rows failed")

    # Apply lazy migrations in batch — best-effort, failure doesn't
    # affect the current search result.
    if lazy_migrations:
        try:
            conn = conn_factory()
            try:
                for row_id, blob in lazy_migrations:
                    conn.execute(
                        "UPDATE rag_items SET embedding_blob = ? WHERE id = ? AND embedding_blob IS NULL",
                        (blob, row_id),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.debug("rag_memory: lazy migration failed (non-fatal)")

    # Vectorized cosine for rows that have an embedding.
    cosine_scores: list[float] = [0.0] * len(parsed_rows)
    if query_embedding and any(have_embedding):
        try:
            import numpy as np

            # Stack only the rows that have embeddings; remember their
            # positions so we can map scores back. Vectors may be
            # numpy arrays (BLOB fast path) or lists (JSON slow path) —
            # np.asarray normalizes both.
            dim = None
            with_idx: list[int] = []
            with_vecs: list[Any] = []
            for i, vec in enumerate(raw_vecs):
                if not have_embedding[i] or vec is None:
                    continue
                vec_len = len(vec)
                if dim is None:
                    dim = vec_len
                if vec_len != dim:
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
            for i, vec in enumerate(raw_vecs):
                if have_embedding[i] and vec is not None:
                    cosine_scores[i] = cosine_sim_func(
                        query_embedding,
                        list(vec) if not isinstance(vec, list) else vec,
                    )

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
            row_dict.pop("embedding", None)       # don't leak vector to caller
            row_dict.pop("embedding_blob", None)  # don't leak BLOB bytes either
            row_dict.pop("text_hash", None)       # internal
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
        # A row counts as "embedded" if it has either the new BLOB or
        # legacy JSON form. The lazy-migration path eventually pushes
        # everything to BLOB, but during transition both can coexist.
        with_embeddings = conn.execute(
            """
            SELECT COUNT(*) FROM rag_items
            WHERE embedding_blob IS NOT NULL OR COALESCE(embedding, '') != ''
            """
        ).fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "total": total, "with_embeddings": with_embeddings, "model": embed_model}

