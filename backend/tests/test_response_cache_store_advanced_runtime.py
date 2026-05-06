"""Tests for two previously uncovered modules:
  response_cache/store — init_db, get_cached, set_cached, clear_cache,
    cache_stats (all use callback injection → testable with in-memory SQLite)
  advanced/runtime — BLOCKED_DIRS, TEXT_EXTS constants; open_project,
    get_project_info, project_tree, read_project_file, search_in_project,
    close_project (global _project_path state, tested with tempfile)
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.advanced.runtime as adv  # noqa: E402
from app.application.response_cache.store import (  # noqa: E402
    init_db,
    get_cached,
    set_cached,
    clear_cache,
    cache_stats,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for response_cache/store tests — in-memory SQLite via callbacks
# ─────────────────────────────────────────────────────────────────────────────

def _make_memory_conn_factory():
    """Return a connect_func that opens the SAME in-memory DB every call."""
    _db = sqlite3.connect(":memory:", check_same_thread=False)
    _db.row_factory = sqlite3.Row

    def connect():
        return _SharedMemoryConn(_db)

    return connect, _db


class _SharedMemoryConn:
    """Thin wrapper: delegates to shared in-memory DB but ignores close()."""
    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def execute(self, *a, **kw):
        return self._db.execute(*a, **kw)

    def commit(self):
        self._db.commit()

    def close(self):
        pass  # keep the shared connection alive


def _normalize(text: str) -> str:
    return (text or "").lower().strip()


def _qhash(norm: str, model: str, profile: str) -> str:
    import hashlib
    return hashlib.sha256(f"{norm}|{model}|{profile}".encode()).hexdigest()


_LONG_QUERY = "explain the theory of relativity in simple terms please"
_LONG_RESP = "A" * 100  # >= 20 chars, no error prefix


# ─────────────────────────────────────────────────────────────────────────────
# response_cache/store — init_db
# ─────────────────────────────────────────────────────────────────────────────

class InitDbTest(unittest.TestCase):
    def test_creates_cache_table(self) -> None:
        conn_func, db = _make_memory_conn_factory()
        init_db(connect_func=conn_func)
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t["name"] for t in tables]
        self.assertIn("cache", table_names)

    def test_idempotent(self) -> None:
        conn_func, _ = _make_memory_conn_factory()
        init_db(connect_func=conn_func)
        # Should not raise on second call
        init_db(connect_func=conn_func)


# ─────────────────────────────────────────────────────────────────────────────
# response_cache/store — get_cached / set_cached
# ─────────────────────────────────────────────────────────────────────────────

class GetSetCachedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._conn_func, _ = _make_memory_conn_factory()
        init_db(connect_func=self._conn_func)

    def _get(self, query: str = _LONG_QUERY) -> str | None:
        return get_cached(
            connect_func=self._conn_func,
            normalize_query_func=_normalize,
            query_hash_func=_qhash,
            cache_ttl=3600,
            query=query,
            model_name="llama3",
            profile_name="default",
        )

    def _set(self, query: str = _LONG_QUERY, response: str = _LONG_RESP) -> None:
        set_cached(
            connect_func=self._conn_func,
            normalize_query_func=_normalize,
            query_hash_func=_qhash,
            max_cache_size=100,
            query=query,
            model_name="llama3",
            profile_name="default",
            response=response,
        )

    def test_miss_returns_none(self) -> None:
        self.assertIsNone(self._get())

    def test_hit_returns_stored_response(self) -> None:
        self._set()
        result = self._get()
        self.assertEqual(result, _LONG_RESP)

    def test_different_model_is_miss(self) -> None:
        self._set()
        result = get_cached(
            connect_func=self._conn_func,
            normalize_query_func=_normalize,
            query_hash_func=_qhash,
            cache_ttl=3600,
            query=_LONG_QUERY,
            model_name="gemma",
            profile_name="default",
        )
        self.assertIsNone(result)

    def test_different_profile_is_miss(self) -> None:
        self._set()
        result = get_cached(
            connect_func=self._conn_func,
            normalize_query_func=_normalize,
            query_hash_func=_qhash,
            cache_ttl=3600,
            query=_LONG_QUERY,
            model_name="llama3",
            profile_name="work",
        )
        self.assertIsNone(result)

    def test_short_query_not_stored(self) -> None:
        set_cached(
            connect_func=self._conn_func,
            normalize_query_func=_normalize,
            query_hash_func=_qhash,
            max_cache_size=100,
            query="short",
            model_name="llama3",
            profile_name="default",
            response=_LONG_RESP,
        )
        result = get_cached(
            connect_func=self._conn_func,
            normalize_query_func=_normalize,
            query_hash_func=_qhash,
            cache_ttl=3600,
            query="short",
            model_name="llama3",
            profile_name="default",
        )
        self.assertIsNone(result)

    def test_short_response_not_stored(self) -> None:
        self._set(response="tiny")
        self.assertIsNone(self._get())

    def test_error_prefix_not_stored(self) -> None:
        self._set(response="⚠️ some error occurred in the system")
        self.assertIsNone(self._get())

    def test_overwrite_same_hash(self) -> None:
        self._set(response="first" + " " * 20)
        self._set(response="second" + " " * 20)
        result = self._get()
        self.assertIn("second", result)

    def test_get_increments_hit_count(self) -> None:
        self._set()
        self._get()
        self._get()
        stats = cache_stats(
            connect_func=self._conn_func,
            max_cache_size=100,
            cache_ttl=3600,
        )
        self.assertGreaterEqual(stats["total_hits"], 2)


# ─────────────────────────────────────────────────────────────────────────────
# response_cache/store — clear_cache
# ─────────────────────────────────────────────────────────────────────────────

class ClearCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._conn_func, _ = _make_memory_conn_factory()
        init_db(connect_func=self._conn_func)

    def test_clear_removes_all_entries(self) -> None:
        set_cached(
            connect_func=self._conn_func,
            normalize_query_func=_normalize,
            query_hash_func=_qhash,
            max_cache_size=100,
            query=_LONG_QUERY,
            model_name="llama3",
            profile_name="default",
            response=_LONG_RESP,
        )
        clear_cache(connect_func=self._conn_func)
        stats = cache_stats(
            connect_func=self._conn_func,
            max_cache_size=100,
            cache_ttl=3600,
        )
        self.assertEqual(stats["total_entries"], 0)

    def test_clear_empty_cache_is_safe(self) -> None:
        try:
            clear_cache(connect_func=self._conn_func)
        except Exception as exc:
            self.fail(f"clear_cache raised: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# response_cache/store — cache_stats
# ─────────────────────────────────────────────────────────────────────────────

class CacheStatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._conn_func, _ = _make_memory_conn_factory()
        init_db(connect_func=self._conn_func)

    def test_returns_dict(self) -> None:
        self.assertIsInstance(
            cache_stats(connect_func=self._conn_func, max_cache_size=100, cache_ttl=3600),
            dict,
        )

    def test_empty_cache_zero_entries(self) -> None:
        stats = cache_stats(connect_func=self._conn_func, max_cache_size=100, cache_ttl=3600)
        self.assertEqual(stats["total_entries"], 0)

    def test_reflects_max_size(self) -> None:
        stats = cache_stats(connect_func=self._conn_func, max_cache_size=42, cache_ttl=3600)
        self.assertEqual(stats["max_size"], 42)

    def test_reflects_ttl(self) -> None:
        stats = cache_stats(connect_func=self._conn_func, max_cache_size=100, cache_ttl=7200)
        self.assertEqual(stats["ttl_seconds"], 7200)

    def test_after_insert_entries_nonzero(self) -> None:
        set_cached(
            connect_func=self._conn_func,
            normalize_query_func=_normalize,
            query_hash_func=_qhash,
            max_cache_size=100,
            query=_LONG_QUERY,
            model_name="llama3",
            profile_name="default",
            response=_LONG_RESP,
        )
        stats = cache_stats(connect_func=self._conn_func, max_cache_size=100, cache_ttl=3600)
        self.assertEqual(stats["total_entries"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# advanced/runtime — BLOCKED_DIRS / TEXT_EXTS constants
# ─────────────────────────────────────────────────────────────────────────────

class AdvancedConstantsTest(unittest.TestCase):
    def test_blocked_dirs_is_set(self) -> None:
        self.assertIsInstance(adv.BLOCKED_DIRS, (set, frozenset))

    def test_blocked_dirs_contains_git(self) -> None:
        self.assertIn(".git", adv.BLOCKED_DIRS)

    def test_blocked_dirs_contains_node_modules(self) -> None:
        self.assertIn("node_modules", adv.BLOCKED_DIRS)

    def test_text_exts_is_set(self) -> None:
        self.assertIsInstance(adv.TEXT_EXTS, (set, frozenset))

    def test_text_exts_contains_py(self) -> None:
        self.assertIn(".py", adv.TEXT_EXTS)

    def test_text_exts_contains_js(self) -> None:
        self.assertIn(".js", adv.TEXT_EXTS)


# ─────────────────────────────────────────────────────────────────────────────
# advanced/runtime — open_project / get_project_info / close_project
# ─────────────────────────────────────────────────────────────────────────────

class OpenCloseProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        adv.close_project()  # ensure clean state
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        adv.close_project()
        self._tmpdir.cleanup()

    def test_open_valid_dir_ok_true(self) -> None:
        result = adv.open_project(self._tmpdir.name)
        self.assertTrue(result["ok"])

    def test_open_stores_path(self) -> None:
        adv.open_project(self._tmpdir.name)
        info = adv.get_project_info()
        self.assertTrue(info["ok"])

    def test_open_nonexistent_ok_false(self) -> None:
        result = adv.open_project("/nonexistent/dir/xyz")
        self.assertFalse(result["ok"])

    def test_open_nonexistent_has_error(self) -> None:
        result = adv.open_project("/nonexistent/dir/xyz")
        self.assertIn("error", result)

    def test_get_info_no_project_open(self) -> None:
        result = adv.get_project_info()
        self.assertFalse(result["ok"])

    def test_get_info_after_open_has_name(self) -> None:
        adv.open_project(self._tmpdir.name)
        info = adv.get_project_info()
        self.assertIn("name", info)

    def test_close_returns_ok(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.close_project()
        self.assertTrue(result["ok"])

    def test_close_clears_project(self) -> None:
        adv.open_project(self._tmpdir.name)
        adv.close_project()
        info = adv.get_project_info()
        self.assertFalse(info["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# advanced/runtime — project_tree
# ─────────────────────────────────────────────────────────────────────────────

class ProjectTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        adv.close_project()
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        # Create some test files/dirs
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
        (root / "src" / "utils.py").write_text("# utils", encoding="utf-8")
        (root / "README.md").write_text("# Project", encoding="utf-8")

    def tearDown(self) -> None:
        adv.close_project()
        self._tmpdir.cleanup()

    def test_no_project_returns_error(self) -> None:
        result = adv.project_tree()
        self.assertFalse(result["ok"])

    def test_with_project_ok_true(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.project_tree()
        self.assertTrue(result["ok"])

    def test_returns_items_list(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.project_tree()
        self.assertIsInstance(result["items"], list)

    def test_finds_py_file(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.project_tree()
        paths = [item["path"] for item in result["items"]]
        self.assertTrue(any("main.py" in p for p in paths))

    def test_finds_dir(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.project_tree()
        types = [item["type"] for item in result["items"]]
        self.assertIn("dir", types)

    def test_count_matches_items_len(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.project_tree()
        self.assertEqual(result["count"], len(result["items"]))


# ─────────────────────────────────────────────────────────────────────────────
# advanced/runtime — read_project_file
# ─────────────────────────────────────────────────────────────────────────────

class ReadProjectFileTest(unittest.TestCase):
    def setUp(self) -> None:
        adv.close_project()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name)
        (self._root / "hello.py").write_text("print('hello')", encoding="utf-8")

    def tearDown(self) -> None:
        adv.close_project()
        self._tmpdir.cleanup()

    def test_no_project_returns_error(self) -> None:
        result = adv.read_project_file("hello.py")
        self.assertFalse(result["ok"])

    def test_reads_existing_file(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("hello.py")
        self.assertTrue(result["ok"])

    def test_content_is_string(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("hello.py")
        self.assertIsInstance(result["content"], str)

    def test_content_correct(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("hello.py")
        self.assertIn("hello", result["content"])

    def test_nonexistent_file_ok_false(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("missing.py")
        self.assertFalse(result["ok"])

    def test_path_traversal_blocked(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("../../etc/passwd")
        self.assertFalse(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# advanced/runtime — search_in_project
# ─────────────────────────────────────────────────────────────────────────────

class SearchInProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        adv.close_project()
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        (root / "alpha.py").write_text("x = hello_world()", encoding="utf-8")
        (root / "beta.py").write_text("y = goodbye()", encoding="utf-8")

    def tearDown(self) -> None:
        adv.close_project()
        self._tmpdir.cleanup()

    def test_no_project_returns_error(self) -> None:
        result = adv.search_in_project("hello")
        self.assertFalse(result["ok"])

    def test_finds_matching_lines(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.search_in_project("hello")
        self.assertTrue(result["ok"])
        self.assertGreater(result["count"], 0)

    def test_items_are_dicts_with_path(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.search_in_project("hello")
        for item in result["items"]:
            self.assertIn("path", item)
            self.assertIn("line", item)
            self.assertIn("text", item)

    def test_no_match_returns_empty_list(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.search_in_project("zzzznotfound99999")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["items"], [])

    def test_query_reflected_in_result(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.search_in_project("hello")
        self.assertEqual(result["query"], "hello")

    def test_max_results_limits_count(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.search_in_project("", max_results=1)
        self.assertLessEqual(result["count"], 1)


if __name__ == "__main__":
    unittest.main()
