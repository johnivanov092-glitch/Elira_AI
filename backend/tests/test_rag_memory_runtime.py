"""Tests for app.application.rag_memory.runtime - all functions use
callback injection, so the entire module is testable with in-memory SQLite
and mock embedding/cosine functions.

Covers:
  cosine_sim (pure math),
  init_db, cleanup_seed_data,
  add_to_rag, search_rag (keyword fallback path),
  get_rag_context, list_rag, delete_rag, rag_stats
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

from app.application.rag_memory.runtime import (  # noqa: E402
    cosine_sim,
    init_db,
    cleanup_seed_data,
    add_to_rag,
    search_rag,
    get_rag_context,
    list_rag,
    delete_rag,
    rag_stats,
)


# ---
# Shared in-memory SQLite helpers (same _SharedConn pattern)
# ---

class _SharedConn:
    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def execute(self, *a, **kw):
        return self._db.execute(*a, **kw)

    def commit(self):
        self._db.commit()

    def close(self):
        pass


def _make_db():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row

    def connect():
        return _SharedConn(db)

    return db, connect


def _bootstrapped():
    db, conn_func = _make_db()
    init_db(conn_factory=conn_func)
    return db, conn_func


# ---
# cosine_sim
# ---

class CosineSimilarityTest(unittest.TestCase):
    def test_identical_vectors_returns_one(self) -> None:
        self.assertAlmostEqual(cosine_sim([1.0, 0.0], [1.0, 0.0]), 1.0, places=5)

    def test_orthogonal_vectors_returns_zero(self) -> None:
        self.assertAlmostEqual(cosine_sim([1.0, 0.0], [0.0, 1.0]), 0.0, places=5)

    def test_opposite_vectors_returns_negative_one(self) -> None:
        self.assertAlmostEqual(cosine_sim([1.0, 0.0], [-1.0, 0.0]), -1.0, places=5)

    def test_empty_a_returns_zero(self) -> None:
        self.assertEqual(cosine_sim([], [1.0, 2.0]), 0.0)

    def test_empty_b_returns_zero(self) -> None:
        self.assertEqual(cosine_sim([1.0, 2.0], []), 0.0)

    def test_both_empty_returns_zero(self) -> None:
        self.assertEqual(cosine_sim([], []), 0.0)

    def test_different_lengths_returns_zero(self) -> None:
        self.assertEqual(cosine_sim([1.0, 2.0], [1.0]), 0.0)

    def test_zero_vector_returns_zero(self) -> None:
        self.assertEqual(cosine_sim([0.0, 0.0], [1.0, 0.0]), 0.0)

    def test_returns_float(self) -> None:
        self.assertIsInstance(cosine_sim([1.0], [1.0]), float)

    def test_partial_overlap(self) -> None:
        result = cosine_sim([1.0, 1.0], [1.0, 0.0])
        self.assertGreater(result, 0.0)
        self.assertLess(result, 1.0)

    def test_range_between_neg_one_and_one(self) -> None:
        result = cosine_sim([3.0, 4.0], [1.0, 2.0])
        self.assertGreaterEqual(result, -1.0)
        self.assertLessEqual(result, 1.0)


# ---
# init_db
# ---

class InitDbTest(unittest.TestCase):
    def test_creates_rag_items_table(self) -> None:
        db, conn_func = _make_db()
        init_db(conn_factory=conn_func)
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertIn("rag_items", tables)

    def test_idempotent(self) -> None:
        _, conn_func = _make_db()
        init_db(conn_factory=conn_func)
        init_db(conn_factory=conn_func)  # should not raise


# ---
# cleanup_seed_data
# ---

class CleanupSeedDataTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped()

    def test_removes_matching_seed_row(self) -> None:
        self._db.execute(
            "INSERT INTO rag_items(text, category) VALUES (?, 'fact')",
            ("seed text",),
        )
        self._db.commit()
        cleanup_seed_data(conn_factory=self._conn_func, seed_rag_text="seed text")
        count = self._db.execute("SELECT COUNT(*) FROM rag_items").fetchone()[0]
        self.assertEqual(count, 0)

    def test_does_not_remove_non_matching(self) -> None:
        self._db.execute(
            "INSERT INTO rag_items(text, category) VALUES (?, 'fact')",
            ("other text",),
        )
        self._db.commit()
        cleanup_seed_data(conn_factory=self._conn_func, seed_rag_text="seed text")
        count = self._db.execute("SELECT COUNT(*) FROM rag_items").fetchone()[0]
        self.assertEqual(count, 1)

    def test_empty_table_does_not_raise(self) -> None:
        try:
            cleanup_seed_data(conn_factory=self._conn_func, seed_rag_text="anything")
        except Exception as exc:
            self.fail(f"cleanup_seed_data raised: {exc}")


# ---
# add_to_rag
# ---

class AddToRagTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped()

    def _add(self, text: str, embed: list | None = None) -> dict:
        return add_to_rag(
            conn_factory=self._conn_func,
            get_embedding_func=lambda t: embed,
            text=text,
        )

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._add("valid text here"), dict)

    def test_ok_true_on_success(self) -> None:
        self.assertTrue(self._add("valid text here")["ok"])

    def test_has_id_on_success(self) -> None:
        result = self._add("valid text here")
        self.assertIn("id", result)

    def test_short_text_rejected(self) -> None:
        result = self._add("ab")
        self.assertFalse(result["ok"])

    def test_empty_text_rejected(self) -> None:
        result = self._add("")
        self.assertFalse(result["ok"])

    def test_has_embedding_false_when_none(self) -> None:
        result = self._add("valid text here", embed=None)
        self.assertFalse(result["has_embedding"])

    def test_has_embedding_true_when_provided(self) -> None:
        result = self._add("valid text here", embed=[0.1, 0.2])
        self.assertTrue(result["has_embedding"])

    def test_row_stored_in_db(self) -> None:
        self._add("stored content here")
        count = self._db.execute("SELECT COUNT(*) FROM rag_items").fetchone()[0]
        self.assertEqual(count, 1)


# ---
# search_rag (keyword path - no real embeddings needed)
# ---

class SearchRagTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped()
        # Pre-load two rows without embeddings
        for text in [
            "Python is a programming language",
            "JavaScript is used for web development",
        ]:
            add_to_rag(
                conn_factory=self._conn_func,
                get_embedding_func=lambda t: None,
                text=text,
            )

    def _search(self, query: str, **kwargs) -> dict:
        return search_rag(
            conn_factory=self._conn_func,
            get_embedding_func=lambda q: None,   # no embeddings: keyword path
            cosine_sim_func=cosine_sim,
            query=query,
            **kwargs,
        )

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._search("python"), dict)

    def test_ok_true(self) -> None:
        self.assertTrue(self._search("python")["ok"])

    def test_empty_query_returns_empty(self) -> None:
        result = self._search("")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["items"], [])

    def test_no_rows_returns_empty(self) -> None:
        _, empty_conn = _bootstrapped()
        result = search_rag(
            conn_factory=empty_conn,
            get_embedding_func=lambda q: None,
            cosine_sim_func=cosine_sim,
            query="python",
        )
        self.assertEqual(result["count"], 0)

    def test_finds_matching_keyword(self) -> None:
        result = self._search("python", min_score=0.0)
        texts = [item["text"] for item in result["items"]]
        self.assertTrue(any("Python" in t for t in texts))

    def test_method_is_keyword(self) -> None:
        result = self._search("python")
        self.assertEqual(result["method"], "keyword")

    def test_limit_respected(self) -> None:
        result = self._search("programming", limit=1, min_score=0.0)
        self.assertLessEqual(result["count"], 1)

    def test_items_have_score(self) -> None:
        result = self._search("python", min_score=0.0)
        for item in result["items"]:
            self.assertIn("score", item)

    def test_no_embedding_key_in_items(self) -> None:
        result = self._search("python", min_score=0.0)
        for item in result["items"]:
            self.assertNotIn("embedding", item)


# ---
# get_rag_context
# ---

class GetRagContextTest(unittest.TestCase):
    def _mock_search(self, items: list) -> Any:
        def search(query: str, limit: int = 5) -> dict:
            return {"ok": True, "items": items, "count": len(items)}
        return search

    def test_returns_string(self) -> None:
        result = get_rag_context(
            search_rag_func=self._mock_search([{"text": "hello world"}]),
            query="hello",
        )
        self.assertIsInstance(result, str)

    def test_empty_items_returns_empty(self) -> None:
        result = get_rag_context(
            search_rag_func=self._mock_search([]),
            query="anything",
        )
        self.assertEqual(result, "")

    def test_context_contains_item_text(self) -> None:
        result = get_rag_context(
            search_rag_func=self._mock_search([{"text": "Python is great"}]),
            query="python",
        )
        self.assertIn("Python is great", result)

    def test_context_has_header(self) -> None:
        result = get_rag_context(
            search_rag_func=self._mock_search([{"text": "some fact"}]),
            query="fact",
        )
        self.assertIn("Context notes", result)

    def test_max_chars_limits_output(self) -> None:
        long_text = "A" * 1000
        result = get_rag_context(
            search_rag_func=self._mock_search([{"text": long_text}]),
            query="test",
            max_chars=50,
        )
        self.assertLessEqual(len(result), 200)  # header + truncation

    def test_multiple_items_joined(self) -> None:
        items = [{"text": "fact one"}, {"text": "fact two"}]
        result = get_rag_context(search_rag_func=self._mock_search(items), query="q")
        self.assertIn("fact one", result)
        self.assertIn("fact two", result)


# ---
# list_rag
# ---

class ListRagTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped()

    def test_returns_dict(self) -> None:
        self.assertIsInstance(list_rag(conn_factory=self._conn_func), dict)

    def test_ok_true(self) -> None:
        self.assertTrue(list_rag(conn_factory=self._conn_func)["ok"])

    def test_empty_db_zero_count(self) -> None:
        result = list_rag(conn_factory=self._conn_func)
        self.assertEqual(result["count"], 0)

    def test_count_after_add(self) -> None:
        add_to_rag(
            conn_factory=self._conn_func,
            get_embedding_func=lambda t: None,
            text="some text to list",
        )
        result = list_rag(conn_factory=self._conn_func)
        self.assertEqual(result["count"], 1)

    def test_items_have_text(self) -> None:
        add_to_rag(
            conn_factory=self._conn_func,
            get_embedding_func=lambda t: None,
            text="listed content here",
        )
        items = list_rag(conn_factory=self._conn_func)["items"]
        self.assertEqual(items[0]["text"], "listed content here")

    def test_limit_respected(self) -> None:
        for i in range(10):
            add_to_rag(
                conn_factory=self._conn_func,
                get_embedding_func=lambda t: None,
                text=f"item number {i} content",
            )
        result = list_rag(conn_factory=self._conn_func, limit=3)
        self.assertLessEqual(result["count"], 3)


# ---
# delete_rag
# ---

class DeleteRagTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped()
        result = add_to_rag(
            conn_factory=self._conn_func,
            get_embedding_func=lambda t: None,
            text="content to delete soon",
        )
        self._item_id = result["id"]

    def test_returns_ok_dict(self) -> None:
        result = delete_rag(conn_factory=self._conn_func, item_id=self._item_id)
        self.assertTrue(result["ok"])

    def test_removes_item(self) -> None:
        delete_rag(conn_factory=self._conn_func, item_id=self._item_id)
        count = self._db.execute("SELECT COUNT(*) FROM rag_items").fetchone()[0]
        self.assertEqual(count, 0)

    def test_nonexistent_id_ok(self) -> None:
        result = delete_rag(conn_factory=self._conn_func, item_id=9999)
        self.assertTrue(result["ok"])


# ---
# rag_stats
# ---

class RagStatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped()

    def _stats(self) -> dict:
        return rag_stats(conn_factory=self._conn_func, embed_model="nomic-embed-text")

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._stats(), dict)

    def test_ok_true(self) -> None:
        self.assertTrue(self._stats()["ok"])

    def test_total_zero_empty(self) -> None:
        self.assertEqual(self._stats()["total"], 0)

    def test_with_embeddings_zero_empty(self) -> None:
        self.assertEqual(self._stats()["with_embeddings"], 0)

    def test_model_reflected(self) -> None:
        self.assertEqual(self._stats()["model"], "nomic-embed-text")

    def test_total_increments_after_add(self) -> None:
        add_to_rag(
            conn_factory=self._conn_func,
            get_embedding_func=lambda t: None,
            text="some rag content here",
        )
        self.assertEqual(self._stats()["total"], 1)

    def test_with_embeddings_count(self) -> None:
        add_to_rag(
            conn_factory=self._conn_func,
            get_embedding_func=lambda t: [0.1, 0.2],
            text="embedded content here too",
        )
        self.assertEqual(self._stats()["with_embeddings"], 1)


if __name__ == "__main__":
    unittest.main()
