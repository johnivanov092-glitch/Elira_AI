"""Tests for rag_memory v2: dedup by text_hash, project-scoped search,
numpy-vectorized cosine similarity, and schema migration.

These pin the three optimizations applied to vector memory:
  1. add_to_rag: same (text + category) → bump importance, don't insert dup
  2. search_rag: project=... filter restricts results to project + global
  3. _batch_cosine: numpy-vectorized matches the pure-Python cosine
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.rag_memory import runtime  # noqa: E402


def _make_conn_factory() -> tuple[Any, Any]:
    """Create an in-memory sqlite DB with a connection factory and a
    long-lived underlying connection (so :memory: state survives
    across factory calls)."""
    holder = sqlite3.connect(":memory:")
    holder.row_factory = sqlite3.Row

    def factory():
        # Return a wrapper that delegates to the holder but does NOT
        # close the underlying connection when caller calls .close().
        class Proxy:
            def __init__(self, real):
                self._real = real

            def execute(self, *a, **kw):
                return self._real.execute(*a, **kw)

            def commit(self):
                return self._real.commit()

            def close(self):
                pass  # keep the in-memory state alive

        return Proxy(holder)

    return factory, holder


class SchemaMigrationTest(unittest.TestCase):
    def test_init_adds_text_hash_and_project_columns(self) -> None:
        factory, holder = _make_conn_factory()
        runtime.init_db(conn_factory=factory)
        cols = {row[1] for row in holder.execute("PRAGMA table_info(rag_items)").fetchall()}
        self.assertIn("text_hash", cols)
        self.assertIn("project", cols)

    def test_init_creates_lookup_indexes(self) -> None:
        factory, holder = _make_conn_factory()
        runtime.init_db(conn_factory=factory)
        idx_names = {row[1] for row in holder.execute("PRAGMA index_list(rag_items)").fetchall()}
        self.assertIn("idx_rag_hash_cat", idx_names)
        self.assertIn("idx_rag_project", idx_names)

    def test_legacy_row_backfilled_with_hash(self) -> None:
        """A row inserted before the schema migration must get a hash
        backfilled when init_db runs again."""
        factory, holder = _make_conn_factory()
        # Build a "legacy" schema (no text_hash)
        holder.execute(
            """
            CREATE TABLE rag_items (
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
        holder.execute("INSERT INTO rag_items (text) VALUES (?)", ("legacy entry",))
        holder.commit()

        runtime.init_db(conn_factory=factory)  # should migrate
        row = holder.execute("SELECT text, text_hash FROM rag_items").fetchone()
        self.assertEqual(row["text"], "legacy entry")
        self.assertNotEqual(row["text_hash"], "")
        self.assertEqual(row["text_hash"], runtime._text_hash("legacy entry"))


class DedupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.factory, self.holder = _make_conn_factory()
        runtime.init_db(conn_factory=self.factory)

    def test_same_text_twice_returns_deduped_action(self) -> None:
        first = runtime.add_to_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: None,
            text="The answer is 42",
            category="fact",
            importance=5,
        )
        self.assertEqual(first.get("action"), "created")
        second = runtime.add_to_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: None,
            text="The answer is 42",
            category="fact",
            importance=5,
        )
        self.assertEqual(second.get("action"), "deduped")
        self.assertEqual(first["id"], second["id"])

    def test_dedup_bumps_importance(self) -> None:
        runtime.add_to_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: None,
            text="X happened",
            category="agent_turn",
            importance=3,
        )
        # Add same text 5 times — importance must climb but stop at 10
        last_imp = 0
        for _ in range(5):
            res = runtime.add_to_rag(
                conn_factory=self.factory,
                get_embedding_func=lambda t: None,
                text="X happened",
                category="agent_turn",
            )
            last_imp = res["importance"]
        self.assertGreater(last_imp, 3)
        self.assertLessEqual(last_imp, 10)

    def test_only_one_row_after_repeated_inserts(self) -> None:
        for _ in range(10):
            runtime.add_to_rag(
                conn_factory=self.factory,
                get_embedding_func=lambda t: None,
                text="duplicate this",
                category="fact",
            )
        count = self.holder.execute("SELECT COUNT(*) FROM rag_items").fetchone()[0]
        self.assertEqual(count, 1)

    def test_different_category_same_text_inserts_both(self) -> None:
        """Dedup is per (text + category). Same text in different
        categories must NOT merge — they may represent different
        things (e.g. a user fact vs an agent_turn summary)."""
        runtime.add_to_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: None,
            text="shared content",
            category="fact",
        )
        res = runtime.add_to_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: None,
            text="shared content",
            category="agent_turn",
        )
        self.assertEqual(res["action"], "created")
        count = self.holder.execute("SELECT COUNT(*) FROM rag_items").fetchone()[0]
        self.assertEqual(count, 2)


class ProjectScopingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.factory, self.holder = _make_conn_factory()
        runtime.init_db(conn_factory=self.factory)

    def _add(self, text: str, project: str = "", importance: int = 5) -> None:
        runtime.add_to_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: None,
            text=text,
            project=project,
            importance=importance,
        )

    def test_search_with_project_includes_globals(self) -> None:
        self._add("user fact about Anna", project="")          # global
        self._add("project A auth notes", project="proj_a")
        self._add("project B login flow", project="proj_b")

        res = runtime.search_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: None,
            cosine_sim_func=runtime.cosine_sim,
            query="auth",
            project="proj_a",
            min_score=0.0,
        )
        texts = [it["text"] for it in res["items"]]
        # project_a's note must be found
        self.assertIn("project A auth notes", texts)
        # project_b's note must NOT leak
        self.assertNotIn("project B login flow", texts)

    def test_search_with_project_excludes_other_projects(self) -> None:
        self._add("alpha owns auth.py", project="alpha")
        self._add("beta owns auth.py", project="beta")
        res = runtime.search_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: None,
            cosine_sim_func=runtime.cosine_sim,
            query="auth",
            project="alpha",
            min_score=0.0,
        )
        texts = " | ".join(it["text"] for it in res["items"])
        self.assertIn("alpha", texts)
        self.assertNotIn("beta", texts)

    def test_search_without_project_returns_everything(self) -> None:
        self._add("alpha thing", project="alpha")
        self._add("beta thing", project="beta")
        self._add("global thing", project="")
        res = runtime.search_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: None,
            cosine_sim_func=runtime.cosine_sim,
            query="thing",
            project=None,
            min_score=0.0,
        )
        texts = " | ".join(it["text"] for it in res["items"])
        self.assertIn("alpha", texts)
        self.assertIn("beta", texts)
        self.assertIn("global", texts)


class NumpyBatchCosineTest(unittest.TestCase):
    """The new _batch_cosine must produce results within float-rounding
    distance of the legacy pure-Python cosine_sim for the same inputs."""

    def test_batch_matches_pure_python_pairwise(self) -> None:
        try:
            import numpy as np
        except Exception:
            self.skipTest("numpy not available")
        query = [0.1, 0.5, -0.3, 0.7, -0.2]
        rows = [
            [0.2, 0.4, -0.1, 0.6, -0.3],
            [-0.5, 0.1, 0.4, -0.2, 0.0],
            [0.7, 0.6, -0.4, 0.5, -0.1],
        ]
        # Reference: pairwise
        expected = [runtime.cosine_sim(query, row) for row in rows]
        # Bulk
        matrix = np.asarray(rows, dtype=np.float32)
        actual = runtime._batch_cosine(query, matrix)
        for exp, got in zip(expected, actual):
            self.assertAlmostEqual(float(exp), float(got), places=5)

    def test_batch_handles_zero_query_norm(self) -> None:
        try:
            import numpy as np
        except Exception:
            self.skipTest("numpy not available")
        # Query is all-zero — all scores should be 0
        result = runtime._batch_cosine([0, 0, 0], np.asarray([[1, 2, 3], [4, 5, 6]], dtype=np.float32))
        for score in result:
            self.assertEqual(float(score), 0.0)


class SearchWithEmbeddingsBatchTest(unittest.TestCase):
    """End-to-end: search_rag with embedded rows must return results
    ranked the same as the pure-Python implementation."""

    def setUp(self) -> None:
        self.factory, self.holder = _make_conn_factory()
        runtime.init_db(conn_factory=self.factory)

    def test_embedded_rows_get_ranked(self) -> None:
        # Inject rows with known embeddings — three rows, with row 1
        # very similar to the query, rows 0 and 2 less so.
        rows = [
            ("apple banana", [0.1, 0.0, 0.0, 0.5]),
            ("the cat purrs",  [0.9, 0.1, 0.0, 0.0]),  # matches query best
            ("rainy day",       [0.0, 0.5, 0.5, 0.0]),
        ]
        for text, vec in rows:
            runtime.add_to_rag(
                conn_factory=self.factory,
                get_embedding_func=lambda t, v=vec: v,
                text=text,
                category="fact",
                importance=5,
            )
        # Query vector aligned with row 1
        result = runtime.search_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: [0.95, 0.0, 0.0, 0.0],
            cosine_sim_func=runtime.cosine_sim,
            query="cat",
            min_score=0.0,
        )
        self.assertEqual(result["method"], "embedding")
        self.assertGreater(len(result["items"]), 0)
        # Best match must be the cat row
        self.assertEqual(result["items"][0]["text"], "the cat purrs")


if __name__ == "__main__":
    unittest.main()
