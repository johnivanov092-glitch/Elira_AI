"""W1 retrieval: web_query over a run's corpus.

BM25 is the base (always works); the local embed service (:8001) is an OPTIONAL
re-rank that fails open — its absence must never break retrieval (contract §5).

Also provides a quote→offset→hash verification primitive for exact excerpts.
The retrieval contract guarantees:
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
import re
from typing import Any

from app.infrastructure.web_corpus import store as _store
from app.application.web_evidence.analyzer import Bm25Index, normalize, tokenize

logger = logging.getLogger(__name__)

_TOP_K_CAP = 8
_QUOTE_MAX = 500


def _quote_pattern(quote: str) -> re.Pattern[str]:
    """Match exact non-whitespace codepoints with flexible whitespace runs."""
    parts = re.split(r"(\s+)", quote)
    return re.compile("".join(
        r"\s+" if part.isspace() else re.escape(part)
        for part in parts
        if part
    ))


def _unique_quote_span(text: str, quote: str) -> tuple[int, int] | None:
    """Return the canonical span only when the normalized match is unique."""
    matches = _quote_pattern(quote).finditer(text)
    first = next(matches, None)
    if first is None or next(matches, None) is not None:
        return None
    return first.start(), first.end()


def _quote_span_at_offset(
    text: str,
    quote: str,
    start: int,
) -> tuple[int, int] | None:
    """Verify exact non-whitespace codepoints at a server-provided offset."""
    if start < 0 or start >= len(text):
        return None
    text_pos = start
    quote_pos = 0
    while quote_pos < len(quote):
        while quote_pos < len(quote) and quote[quote_pos].isspace():
            quote_pos += 1
        while text_pos < len(text) and text[text_pos].isspace():
            text_pos += 1
        if quote_pos >= len(quote):
            break
        if text_pos >= len(text) or text[text_pos] != quote[quote_pos]:
            return None
        text_pos += 1
        quote_pos += 1
    return start, text_pos


def _snippet(chunk_text: str, chunk_offset: int, query: str,
             width: int = _QUOTE_MAX) -> tuple[str, int]:
    """Select a verbatim window covering the most distinct query terms.

    A common word near the start must not hide the specific API/fact later in
    the chunk. Offsets still point into the original text for verify_quote.
    """
    if len(chunk_text) <= width:
        return chunk_text, chunk_offset
    low = normalize(chunk_text)
    terms = set(tokenize(query, stem=False))
    if not terms:
        return chunk_text[:width], chunk_offset
    starts = {0}
    for term in terms:
        for match in re.finditer(re.escape(term), low):
            starts.add(max(0, match.start() - width // 3))
    # Prefer the earliest equally informative window for deterministic output.
    start = max(sorted(starts), key=lambda pos: len(
        terms & set(tokenize(chunk_text[pos:pos + width], stem=False))
    ))
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
    * quote_verified — non-whitespace codepoints match exactly while whitespace
      runs may differ; without `offset`, the matching span must be unique;
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
        span = _quote_span_at_offset(text, q, start)
        ok = span is not None
        return {"quote_verified": bool(ok), "source_verified": True,
                "offset": start if ok else None,
                "canonical_quote": text[span[0]:span[1]] if span else None,
                "reason": None if ok else "цитата не найдена по заявленному offset"}
    span = _unique_quote_span(text, q)
    return {"quote_verified": span is not None, "source_verified": True,
            "offset": span[0] if span else None,
            "canonical_quote": text[span[0]:span[1]] if span else None,
            "reason": None if span is not None else "цитата не найдена в источнике дословно"}
