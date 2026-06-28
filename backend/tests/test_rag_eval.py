"""P2.9 — RAG recall eval harness.

evaluate_recall measures hit-rate@k / MRR over a golden set so ranking changes
(e.g. the hybrid reranker) can be validated as improvements, not regressions.
Exercised end-to-end against an in-memory rag corpus in keyword mode
(deterministic, no embeddings needed).
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.rag_memory import runtime  # noqa: E402
from app.application.rag_memory.eval import evaluate_recall  # noqa: E402


def _make_conn_factory() -> tuple[Any, Any]:
    holder = sqlite3.connect(":memory:")
    holder.row_factory = sqlite3.Row

    def factory():
        class Proxy:
            def __init__(self, real):
                self._real = real

            def execute(self, *a, **kw):
                return self._real.execute(*a, **kw)

            def commit(self):
                return self._real.commit()

            def close(self):
                pass

        return Proxy(holder)

    return factory, holder


class RagEvalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.factory, self.holder = _make_conn_factory()
        runtime.init_db(conn_factory=self.factory)
        for text in (
            "python async await tutorial",
            "rust ownership and borrowing guide",
            "sqlite index performance tips",
        ):
            runtime.add_to_rag(
                conn_factory=self.factory,
                get_embedding_func=lambda t: None,  # keyword mode
                text=text,
                category="fact",
                importance=5,
            )

    def _search(self, query: str) -> dict[str, Any]:
        return runtime.search_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: None,
            cosine_sim_func=runtime.cosine_sim,
            query=query,
            min_score=0.0,
        )

    def test_all_golden_queries_hit(self) -> None:
        golden = [
            {"query": "async python", "expect": "python async"},
            {"query": "rust borrowing ownership", "expect": "rust ownership"},
            {"query": "sqlite index", "expect": "sqlite index"},
        ]
        report = evaluate_recall(search_func=self._search, golden=golden, top_k=5)
        self.assertEqual(report["cases"], 3)
        self.assertEqual(report["hit_rate_at_k"], 1.0)
        self.assertGreater(report["mrr"], 0.0)
        self.assertTrue(all(d["hit"] for d in report["details"]))

    def test_miss_lowers_hit_rate(self) -> None:
        golden = [
            {"query": "async python", "expect": "python async"},      # hit
            {"query": "haskell monads", "expect": "haskell monads"},  # miss (not in corpus)
        ]
        report = evaluate_recall(search_func=self._search, golden=golden, top_k=5)
        self.assertEqual(report["hit_rate_at_k"], 0.5)
        misses = [d for d in report["details"] if not d["hit"]]
        self.assertEqual(len(misses), 1)
        self.assertEqual(misses[0]["rank"], None)


if __name__ == "__main__":
    unittest.main()
