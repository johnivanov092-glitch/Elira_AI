"""RAG recall evaluation — measure retrieval quality over a golden set.

Local, deterministic, model-free (beyond whatever ``search_func`` already uses).
Lets us verify that ranking changes (e.g. the hybrid reranker) improve — not
regress — recall: hit-rate@k and MRR over (query, expected-substring) pairs.

A golden case is ``{"query": str, "expect": str}`` where ``expect`` is a
substring that must appear in the text of a retrieved item for a hit.
"""

from __future__ import annotations

from typing import Any, Callable


def evaluate_recall(
    *,
    search_func: Callable[[str], dict[str, Any]],
    golden: list[dict[str, str]],
    top_k: int = 5,
) -> dict[str, Any]:
    """Run each golden query through ``search_func`` and score retrieval.

    Returns hit_rate@k (fraction of queries whose expected substring appears in
    the top-k items), MRR (mean reciprocal rank of the first hit), and per-query
    details. ``search_func(query)`` must return a dict with an ``items`` list of
    rows carrying a ``text`` field (the search_rag / recall shape).
    """
    hits = 0
    rr_sum = 0.0
    details: list[dict[str, Any]] = []
    for case in golden:
        query = str(case.get("query", ""))
        expect = str(case.get("expect", "")).lower()
        items = ((search_func(query) or {}).get("items") or [])[:top_k]
        rank: int | None = None
        for idx, item in enumerate(items, 1):
            if expect and expect in str(item.get("text") or "").lower():
                rank = idx
                break
        if rank is not None:
            hits += 1
            rr_sum += 1.0 / rank
        details.append(
            {"query": query, "expect": case.get("expect", ""), "hit": rank is not None, "rank": rank}
        )

    n = max(1, len(golden))
    return {
        "cases": len(golden),
        "hit_rate_at_k": round(hits / n, 4),
        "mrr": round(rr_sum / n, 4),
        "top_k": top_k,
        "details": details,
    }
