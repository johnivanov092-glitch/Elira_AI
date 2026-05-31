"""Tests for BLOB embedding storage and lazy JSON→BLOB migration."""
from __future__ import annotations

import json
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


class BlobRoundTripTest(unittest.TestCase):
    def test_embedding_to_blob_to_array_round_trip(self) -> None:
        try:
            import numpy as np
        except Exception:
            self.skipTest("numpy not available")
        original = [0.1, 0.5, -0.3, 0.7, -0.2, 0.123456]
        blob = runtime._embedding_to_blob(original)
        self.assertIsNotNone(blob)
        # 6 floats × 4 bytes = 24 bytes
        self.assertEqual(len(blob), 24)
        restored = runtime._blob_to_array(blob)
        for orig, got in zip(original, restored):
            self.assertAlmostEqual(orig, float(got), places=5)

    def test_empty_or_none_returns_none(self) -> None:
        self.assertIsNone(runtime._embedding_to_blob(None))
        self.assertIsNone(runtime._embedding_to_blob([]))
        self.assertIsNone(runtime._blob_to_array(None))
        self.assertIsNone(runtime._blob_to_array(b""))

    def test_blob_is_more_compact_than_json(self) -> None:
        # A 768-dim embedding fits in 3072 bytes as BLOB but ~12-15KB as JSON
        try:
            import numpy as np
        except Exception:
            self.skipTest("numpy not available")
        rng = np.random.default_rng(42)
        vec = rng.standard_normal(768).astype(float).tolist()
        blob = runtime._embedding_to_blob(vec)
        json_text = json.dumps(vec)
        self.assertEqual(len(blob), 768 * 4)
        # BLOB must be at least 3x more compact than JSON
        self.assertLess(len(blob) * 3, len(json_text))


class NewWriteUsesBlobTest(unittest.TestCase):
    def setUp(self) -> None:
        self.factory, self.holder = _make_conn_factory()
        runtime.init_db(conn_factory=self.factory)

    def test_add_to_rag_writes_blob_not_json(self) -> None:
        runtime.add_to_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: [0.1, 0.2, 0.3, 0.4],
            text="test entry",
        )
        row = self.holder.execute(
            "SELECT embedding, embedding_blob FROM rag_items"
        ).fetchone()
        # JSON column must NOT contain the vector (new rows only write BLOB)
        self.assertEqual(row["embedding"], "")
        # BLOB column must have data
        self.assertIsNotNone(row["embedding_blob"])
        self.assertEqual(len(row["embedding_blob"]), 4 * 4)  # 4 floats × 4 bytes


class SearchReadsBlobTest(unittest.TestCase):
    def setUp(self) -> None:
        self.factory, self.holder = _make_conn_factory()
        runtime.init_db(conn_factory=self.factory)

    def test_search_with_blob_data_ranks_correctly(self) -> None:
        rows = [
            ("not relevant",  [0.1, 0.0, 0.0, 0.5]),
            ("best match",    [0.95, 0.0, 0.0, 0.0]),
            ("middle",         [0.5, 0.5, 0.5, 0.0]),
        ]
        for text, vec in rows:
            runtime.add_to_rag(
                conn_factory=self.factory,
                get_embedding_func=lambda t, v=vec: v,
                text=text,
            )
        result = runtime.search_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: [1.0, 0.0, 0.0, 0.0],
            cosine_sim_func=runtime.cosine_sim,
            query="test",
            min_score=0.0,
        )
        self.assertEqual(result["method"], "embedding")
        self.assertEqual(result["items"][0]["text"], "best match")

    def test_result_items_dont_leak_internal_columns(self) -> None:
        runtime.add_to_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: [0.1, 0.2, 0.3],
            text="test",
        )
        result = runtime.search_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: [0.1, 0.2, 0.3],
            cosine_sim_func=runtime.cosine_sim,
            query="test",
            min_score=0.0,
        )
        item = result["items"][0]
        # Internal storage columns must not leak to the caller
        self.assertNotIn("embedding", item)
        self.assertNotIn("embedding_blob", item)
        self.assertNotIn("text_hash", item)


class LazyMigrationTest(unittest.TestCase):
    """Legacy rows have JSON in `embedding` and NULL in `embedding_blob`.
    First search must read JSON AND write back BLOB so the next search
    hits the fast path."""

    def setUp(self) -> None:
        self.factory, self.holder = _make_conn_factory()
        runtime.init_db(conn_factory=self.factory)

    def _insert_legacy_row(self, text: str, vec: list[float]) -> int:
        """Direct INSERT bypassing add_to_rag so we simulate pre-BLOB data
        (JSON populated, BLOB null)."""
        cur = self.holder.execute(
            "INSERT INTO rag_items (text, text_hash, category, embedding, importance) VALUES (?, ?, ?, ?, ?)",
            (text, runtime._text_hash(text), "fact", json.dumps(vec), 5),
        )
        self.holder.commit()
        return cur.lastrowid

    def test_search_migrates_legacy_row_to_blob(self) -> None:
        row_id = self._insert_legacy_row("legacy entry", [0.9, 0.1, 0.0, 0.0])
        # Before search: no blob
        pre = self.holder.execute(
            "SELECT embedding_blob FROM rag_items WHERE id = ?", (row_id,)
        ).fetchone()
        self.assertIsNone(pre["embedding_blob"])

        # Run a search (any) — this should populate the blob lazily
        runtime.search_rag(
            conn_factory=self.factory,
            get_embedding_func=lambda t: [1.0, 0.0, 0.0, 0.0],
            cosine_sim_func=runtime.cosine_sim,
            query="anything",
            min_score=0.0,
        )

        # After search: blob exists
        post = self.holder.execute(
            "SELECT embedding_blob FROM rag_items WHERE id = ?", (row_id,)
        ).fetchone()
        self.assertIsNotNone(post["embedding_blob"])
        self.assertEqual(len(post["embedding_blob"]), 4 * 4)

    def test_init_db_one_shot_migrates_old_rows(self) -> None:
        """If a fresh `init_db` runs against a DB that already has JSON-
        only rows (e.g. on restart after upgrading from v1), the migration
        block in init_db converts them all in batch."""
        # Build a v1 (pre-BLOB) schema and insert a row
        self.holder.execute("DROP TABLE rag_items")
        self.holder.execute(
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
        self.holder.execute(
            "INSERT INTO rag_items (text, embedding) VALUES (?, ?)",
            ("legacy", json.dumps([0.1, 0.2, 0.3, 0.4])),
        )
        self.holder.commit()

        # Run init_db — must migrate that row
        runtime.init_db(conn_factory=self.factory)

        blob = self.holder.execute(
            "SELECT embedding_blob FROM rag_items"
        ).fetchone()["embedding_blob"]
        self.assertIsNotNone(blob)
        self.assertEqual(len(blob), 4 * 4)


class StatsCountsBothFormatsTest(unittest.TestCase):
    def test_rag_stats_counts_blob_and_legacy_json(self) -> None:
        factory, holder = _make_conn_factory()
        runtime.init_db(conn_factory=factory)
        # One new-style row (BLOB)
        runtime.add_to_rag(
            conn_factory=factory,
            get_embedding_func=lambda t: [0.1, 0.2, 0.3],
            text="new style",
        )
        # One legacy row (JSON only, no BLOB)
        holder.execute(
            "INSERT INTO rag_items (text, text_hash, embedding) VALUES (?, ?, ?)",
            ("legacy style", runtime._text_hash("legacy style"), json.dumps([0.5, 0.6, 0.7])),
        )
        holder.commit()
        stats = runtime.rag_stats(conn_factory=factory, embed_model="nomic-embed-text")
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["with_embeddings"], 2)


if __name__ == "__main__":
    unittest.main()
