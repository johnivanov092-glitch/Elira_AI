"""W1 retrieval: web_query over a run's corpus.

BM25 is the base (always works); the local embed service (:8001) is an OPTIONAL
re-rank that fails open — its absence must never break retrieval (contract §5).
Also the quote→offset→hash verification primitive (contract §11.3), which the
W3 ledger will build on: a quote is verified ONLY when it is found verbatim in
the stored canonical_text — provenance, deterministic, never model-asserted.
"""
from __future__ import annotations

import logging
from typing import Any

from app.application.web_evidence import store as _store
from app.application.web_evidence.analyzer import Bm25Index, normalize, tokenize

logger = logging.getLogger(__name__)

_TOP_K_CAP = 8
_QUOTE_MAX = 500


def _snippet(text: str, query: str, width: int = _QUOTE_MAX) -> str:
    """A window of `text` centered on the earliest query-term hit — so a fact
    buried mid-chunk actually reaches the model, not the chunk's filler head.
    Falls back to the head when nothing matches."""
    if len(text) <= width:
        return text.strip()
    low = normalize(text)
    pos = -1
    for term in tokenize(query, stem=False):
        p = low.find(term)
        if p != -1 and (pos == -1 or p < pos):
            pos = p
    if pos == -1:
        return text[:width].strip()
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def _embed_rerank(query: str, candidates: list[dict]) -> list[dict] | None:
    """Cosine re-rank via :8001. Returns re-ordered candidates, or None to signal
    'embed unavailable — keep BM25 order' (fail-open, one log line)."""
    try:
        from app.infrastructure.llm.openai_compatible import embed_text, is_local_embed_enabled
        if not is_local_embed_enabled():
            return None
        qv = embed_text(query)
        if not qv:
            return None
        import math
        def cos(a, b):
            num = sum(x * y for x, y in zip(a, b))
            da = math.sqrt(sum(x * x for x in a)) or 1.0
            db = math.sqrt(sum(y * y for y in b)) or 1.0
            return num / (da * db)
        scored = []
        for c in candidates:
            cv = embed_text(c["text"][:1000])
            scored.append((cos(qv, cv) if cv else -1.0, c))
        if all(s < 0 for s, _ in scored):
            return None
        scored.sort(key=lambda sc: sc[0], reverse=True)
        return [c for _s, c in scored]
    except Exception as exc:  # noqa: BLE001 — fail-open to BM25
        logger.warning("embed re-rank unavailable (%s) — BM25 order kept", exc)
        return None


def web_query(run_id: str, query: str, *, doc_id: str | None = None,
              top_k: int = 6) -> dict[str, Any]:
    """Top-k chunks from the run's corpus for `query`. Each result carries the
    exact quote + doc_id + chunk_id + offset — ready-made evidence candidates."""
    top_k = max(1, min(int(top_k), _TOP_K_CAP))
    chunks = _store.load_chunks(run_id)
    if doc_id:
        chunks = [c for c in chunks if c["doc_id"] == doc_id]
    if not chunks:
        return {"ok": True, "results": [], "note": "corpus пуст — сначала web_fetch(store=true)"}

    index = Bm25Index()
    by_key: dict[str, dict] = {}
    for c in chunks:
        key = f"{c['doc_id']}:{c['chunk_id']}"
        by_key[key] = c
        index.add(key, c["text"])

    hits = index.search(query, top_k=top_k * 3)
    candidates = [by_key[k] for k, _score in hits] or chunks[:top_k]
    reranked = _embed_rerank(query, candidates)
    final = (reranked or candidates)[:top_k]

    docs = {d["doc_id"]: d for d in _store.list_documents(run_id)}
    results = []
    for c in final:
        d = docs.get(c["doc_id"], {})
        results.append({
            "doc_id": c["doc_id"], "chunk_id": c["chunk_id"], "offset": c["offset"],
            "url": d.get("final_url") or d.get("url"), "title": d.get("title"),
            "quote": _snippet(c["text"], query),
        })
    return {"ok": True, "results": results,
            "ranker": "bm25+embed" if reranked else "bm25"}


def verify_quote(run_id: str, doc_id: str, quote: str) -> dict[str, Any]:
    """Deterministic provenance check (contract §11.3): the quote must appear
    VERBATIM in the stored canonical_text. Case/ё-insensitive per `normalize`, but
    NOT stemmed — exact-phrase never depends on the analyzer. Returns
    {quote_verified, source_verified, offset?}."""
    doc = _store.get_document(run_id, doc_id)
    if not doc:
        return {"quote_verified": False, "source_verified": False,
                "reason": "документ не в корпусе этого рана"}
    q = normalize((quote or "").strip())
    if not q:
        return {"quote_verified": False, "source_verified": True, "reason": "пустая цитата"}
    hay = normalize(doc["canonical_text"])
    idx = hay.find(q)
    return {"quote_verified": idx >= 0, "source_verified": True,
            "offset": idx if idx >= 0 else None,
            "reason": None if idx >= 0 else "цитата не найдена в источнике дословно"}
