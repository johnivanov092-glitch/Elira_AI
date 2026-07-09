"""W1 retrieval: web_query over a run's corpus.

BM25 is the base (always works); the local embed service (:8001) is an OPTIONAL
re-rank that fails open — its absence must never break retrieval (contract §5).

Also the quote→offset→hash verification primitive (contract §11.3) the W3 ledger
builds on. John's W1 review contract, all three fixed here:
  * the `quote` field is VERBATIM canonical_text (no ellipses — display dressing
    never contaminates the evidence field);
  * `offset` is the ABSOLUTE offset of the quote in canonical_text (chunk offset
    + local position), so quote/offset round-trips through verify_quote;
  * verify_quote RECOMPUTES the document hash — a tampered canonical_text fails
    verification instead of certifying a forged quote.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.infrastructure.web_corpus import store as _store
from app.application.web_evidence.analyzer import Bm25Index, normalize, tokenize

logger = logging.getLogger(__name__)

_TOP_K_CAP = 8
_QUOTE_MAX = 500


def _snippet(chunk_text: str, chunk_offset: int, query: str,
             width: int = _QUOTE_MAX) -> tuple[str, int]:
    """(verbatim_quote, absolute_offset): a window of the chunk centered on the
    earliest query-term hit — a fact buried mid-chunk reaches the model, not the
    chunk's filler head. NO ellipses: the quote must round-trip verify_quote."""
    if len(chunk_text) <= width:
        return chunk_text, chunk_offset
    low = normalize(chunk_text)
    pos = -1
    for term in tokenize(query, stem=False):
        p = low.find(term)
        if p != -1 and (pos == -1 or p < pos):
            pos = p
    if pos == -1:
        return chunk_text[:width], chunk_offset
    start = max(0, pos - width // 3)
    end = min(len(chunk_text), start + width)
    return chunk_text[start:end], chunk_offset + start


def _embed_rerank(query: str, candidates: list[dict]) -> list[dict] | None:
    """Cosine re-rank via :8001. None = 'embed unavailable — keep BM25 order'
    (fail-open, one log line)."""
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
    """Top-k excerpts from the run's corpus for `query`. Each result carries a
    VERBATIM quote + doc_id + chunk_id + absolute offset — ready-made evidence
    candidates that round-trip verify_quote."""
    top_k = max(1, min(int(top_k), _TOP_K_CAP))
    try:
        chunks = _store.load_chunks(run_id)
    except _store.StoreUnavailable as exc:
        return {"ok": False, "error": str(exc)}
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

    try:
        docs = {d["doc_id"]: d for d in _store.list_documents(run_id)}
    except _store.StoreUnavailable:
        docs = {}
    results = []
    for c in final:
        d = docs.get(c["doc_id"], {})
        quote, abs_offset = _snippet(c["text"], c["offset"], query)
        try:
            full_doc = _store.get_document(run_id, c["doc_id"])
        except _store.StoreUnavailable:
            full_doc = None
        if full_doc:
            exact_offset = str(full_doc.get("canonical_text") or "").find(quote)
            if exact_offset >= 0:
                abs_offset = exact_offset
        results.append({
            "doc_id": c["doc_id"], "chunk_id": c["chunk_id"], "offset": abs_offset,
            "url": d.get("final_url") or d.get("url"), "title": d.get("title"),
            "quote": quote,
        })
    return {"ok": True, "results": results,
            "ranker": "bm25+embed" if reranked else "bm25"}


def verify_quote(run_id: str, doc_id: str, quote: str,
                 offset: int | None = None) -> dict[str, Any]:
    """Deterministic provenance check (contract §11.3):

    * integrity — the stored canonical_text re-hashes to the recorded
      content_hash (a tampered corpus can NOT certify a quote);
    * quote_verified — the quote appears VERBATIM (exact codepoints) in
      canonical_text; with `offset` given, exactly there;
    * source_verified — the document was really fetched by this run.
    """
    try:
        doc = _store.get_document(run_id, doc_id)
    except _store.StoreUnavailable as exc:
        return {"quote_verified": False, "source_verified": False, "reason": str(exc)}
    if not doc:
        return {"quote_verified": False, "source_verified": False,
                "reason": "документ не в корпусе этого рана"}
    text = doc["canonical_text"]
    actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if actual_hash != doc["content_hash"]:
        return {"quote_verified": False, "source_verified": False,
                "reason": "нарушена целостность корпуса: hash канонического текста "
                          "не совпадает с записанным"}
    q = str(quote or "")
    if not q.strip():
        return {"quote_verified": False, "source_verified": True, "reason": "пустая цитата"}
    if offset is not None:
        try:
            start = int(offset)
        except (TypeError, ValueError):
            start = -1
        ok = start >= 0 and text[start:start + len(q)] == q
        return {"quote_verified": bool(ok), "source_verified": True,
                "offset": start if ok else None,
                "reason": None if ok else "цитата не найдена по заявленному offset"}
    idx = text.find(q)
    return {"quote_verified": idx >= 0, "source_verified": True,
            "offset": idx if idx >= 0 else None,
            "reason": None if idx >= 0 else "цитата не найдена в источнике дословно"}
