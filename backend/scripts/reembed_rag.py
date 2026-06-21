"""One-off: re-embed all rag_items with the server embedding endpoint.

Existing vectors were built with local OpenAI-compatible local LLM nomic (768-dim). After switching
RAG to the server's Qwen3-Embedding endpoint (1024-dim), every row must be
re-embedded into the new vector space — mixed dimensions would break cosine
search. Idempotent: safe to re-run. Back up data/rag_memory.db first.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:  # load the developer env so local_embed_config() sees LOCAL_EMBED_*
    from dotenv import load_dotenv

    load_dotenv(BACKEND / ".env.local")
except Exception:
    pass

from app.application.rag_memory.runtime import _embedding_to_blob  # noqa: E402
from app.application.rag_memory.service import _conn  # noqa: E402
from app.infrastructure.llm.openai_compatible import local_embed_config  # noqa: E402

_CFG = local_embed_config()
EMBED_URL = f"{_CFG.base_url}/embeddings"
EMBED_MODEL = _CFG.model
BATCH = 64


def embed_batch(texts: list[str]) -> list[list[float]]:
    resp = requests.post(
        EMBED_URL,
        headers={"Authorization": f"Bearer {_CFG.api_key}"},
        json={"model": EMBED_MODEL, "input": texts},
        timeout=120,
    )
    resp.raise_for_status()
    rows = resp.json()["data"]
    rows.sort(key=lambda r: r["index"])
    return [r["embedding"] for r in rows]


def main() -> int:
    conn = _conn()
    try:
        rows = conn.execute("SELECT id, text FROM rag_items ORDER BY id").fetchall()
    finally:
        conn.close()
    total = len(rows)
    print(f"rows to re-embed: {total}")
    done = 0
    failed = 0
    t0 = time.monotonic()
    for start in range(0, total, BATCH):
        chunk = rows[start : start + BATCH]
        ids = [r["id"] for r in chunk]
        texts = [str(r["text"]) for r in chunk]
        try:
            vecs = embed_batch(texts)
        except Exception as exc:
            failed += len(chunk)
            print(f"  batch @{start} FAILED: {exc}")
            continue
        conn = _conn()
        try:
            for rid, vec in zip(ids, vecs):
                conn.execute(
                    "UPDATE rag_items SET embedding_blob = ?, embedding = '' WHERE id = ?",
                    (_embedding_to_blob(vec), rid),
                )
            conn.commit()
        finally:
            conn.close()
        done += len(chunk)
        if start % (BATCH * 8) == 0 or done == total:
            print(f"  {done}/{total} ({done * 100 // total}%)")
    dt = time.monotonic() - t0
    print(f"done: {done} re-embedded, {failed} failed, {dt:.1f}s")
    # verify dims are uniform now
    conn = _conn()
    try:
        n_blob = conn.execute(
            "SELECT COUNT(*) FROM rag_items WHERE embedding_blob IS NOT NULL"
        ).fetchone()[0]
        sample = conn.execute(
            "SELECT embedding_blob FROM rag_items WHERE embedding_blob IS NOT NULL LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    dim = len(sample[0]) // 4 if sample else 0
    print(f"with_embedding={n_blob}, sample_dim={dim}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
