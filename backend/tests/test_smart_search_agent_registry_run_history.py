"""Tests for three previously uncovered modules:
  smart_memory/search (tokenize, similarity, STOP_WORDS, search_memory,
                       get_relevant_context — with patched DB_PATH)
  agent_registry/store (row_to_dict pure function)
  run_history/store (load_legacy_runs pure file-reader)
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.smart_memory import search as sm_search  # noqa: E402
from app.application.smart_memory import store as sm_store    # noqa: E402
from app.application.agent_registry import store as ar_store  # noqa: E402
from app.application.run_history import store as rh_store     # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# smart_memory/search — STOP_WORDS
# ─────────────────────────────────────────────────────────────────────────────

class StopWordsTest(unittest.TestCase):
    def test_is_set(self) -> None:
        self.assertIsInstance(sm_search.STOP_WORDS, set)

    def test_not_empty(self) -> None:
        self.assertGreater(len(sm_search.STOP_WORDS), 0)

    def test_contains_english_the(self) -> None:
        self.assertIn("the", sm_search.STOP_WORDS)

    def test_contains_russian_i(self) -> None:
        self.assertIn("и", sm_search.STOP_WORDS)

    def test_contains_strings(self) -> None:
        for word in sm_search.STOP_WORDS:
            self.assertIsInstance(word, str)


# ─────────────────────────────────────────────────────────────────────────────
# smart_memory/search — tokenize
# ─────────────────────────────────────────────────────────────────────────────

class TokenizeTest(unittest.TestCase):
    def test_returns_list(self) -> None:
        self.assertIsInstance(sm_search.tokenize("hello world"), list)

    def test_basic_words(self) -> None:
        tokens = sm_search.tokenize("Python programming")
        self.assertIn("python", tokens)
        self.assertIn("programming", tokens)

    def test_lowercases(self) -> None:
        tokens = sm_search.tokenize("Hello WORLD")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)

    def test_removes_stop_words(self) -> None:
        tokens = sm_search.tokenize("the cat in the hat")
        self.assertNotIn("the", tokens)
        self.assertNotIn("in", tokens)

    def test_removes_short_words(self) -> None:
        # Words with length <= 1 are excluded
        tokens = sm_search.tokenize("a b hello")
        self.assertNotIn("a", tokens)
        self.assertNotIn("b", tokens)
        self.assertIn("hello", tokens)

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(sm_search.tokenize(""), [])

    def test_none_returns_empty(self) -> None:
        self.assertEqual(sm_search.tokenize(None), [])  # type: ignore[arg-type]

    def test_russian_words_tokenized(self) -> None:
        tokens = sm_search.tokenize("изучить Python программирование")
        # "изучить" is not a stop word
        self.assertIn("изучить", tokens)
        self.assertIn("python", tokens)

    def test_stop_word_and_filtered(self) -> None:
        # "и" is in STOP_WORDS — should be filtered
        tokens = sm_search.tokenize("кошка и собака")
        self.assertNotIn("и", tokens)
        self.assertIn("кошка", tokens)


# ─────────────────────────────────────────────────────────────────────────────
# smart_memory/search — similarity
# ─────────────────────────────────────────────────────────────────────────────

class SimilarityTest(unittest.TestCase):
    def test_returns_float(self) -> None:
        self.assertIsInstance(sm_search.similarity("hello world", "hello there"), float)

    def test_identical_texts_one(self) -> None:
        # Identical non-stop-word texts → score = 1.0
        s = sm_search.similarity("python programming", "python programming")
        self.assertAlmostEqual(s, 1.0)

    def test_disjoint_texts_zero(self) -> None:
        s = sm_search.similarity("python code", "french cooking")
        self.assertAlmostEqual(s, 0.0)

    def test_partial_overlap(self) -> None:
        s = sm_search.similarity("python code analysis", "code review")
        self.assertGreater(s, 0.0)
        self.assertLess(s, 1.0)

    def test_empty_left_zero(self) -> None:
        self.assertAlmostEqual(sm_search.similarity("", "hello"), 0.0)

    def test_empty_right_zero(self) -> None:
        self.assertAlmostEqual(sm_search.similarity("hello", ""), 0.0)

    def test_both_empty_zero(self) -> None:
        self.assertAlmostEqual(sm_search.similarity("", ""), 0.0)

    def test_score_between_zero_and_one(self) -> None:
        s = sm_search.similarity("the quick brown fox", "the lazy brown dog")
        self.assertGreaterEqual(s, 0.0)
        self.assertLessEqual(s, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# smart_memory/search — search_memory (patched DB)
# ─────────────────────────────────────────────────────────────────────────────

class SearchMemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = sm_store.DB_PATH
        sm_store.DB_PATH = Path(self._tmpdir.name) / "smart_memory.db"
        sm_store.init_memory_db()

    def tearDown(self) -> None:
        sm_store.DB_PATH = self._orig_db
        self._tmpdir.cleanup()

    def _add(self, text: str, profile: str = "default") -> None:
        sm_store.add_memory(text=text, profile_name=profile)

    def test_empty_query_returns_ok(self) -> None:
        result = sm_search.search_memory("")
        self.assertTrue(result["ok"])

    def test_empty_query_items_empty(self) -> None:
        result = sm_search.search_memory("")
        self.assertEqual(result["items"], [])

    def test_returns_ok_key(self) -> None:
        result = sm_search.search_memory("python")
        self.assertIn("ok", result)

    def test_returns_items_key(self) -> None:
        result = sm_search.search_memory("python")
        self.assertIn("items", result)

    def test_no_matches_returns_empty(self) -> None:
        result = sm_search.search_memory("xyzunmatchedterm")
        self.assertEqual(result["count"], 0)

    def test_finds_matching_memory(self) -> None:
        self._add("I love Python programming language")
        result = sm_search.search_memory("Python programming")
        self.assertGreater(result["count"], 0)

    def test_result_items_are_dicts(self) -> None:
        self._add("Python is great for scripting tasks")
        result = sm_search.search_memory("Python scripting")
        for item in result["items"]:
            self.assertIsInstance(item, dict)

    def test_count_matches_items_length(self) -> None:
        self._add("test memory for counting")
        result = sm_search.search_memory("test memory counting")
        self.assertEqual(result["count"], len(result["items"]))


# ─────────────────────────────────────────────────────────────────────────────
# smart_memory/search — get_relevant_context (patched DB)
# ─────────────────────────────────────────────────────────────────────────────

class GetRelevantContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = sm_store.DB_PATH
        sm_store.DB_PATH = Path(self._tmpdir.name) / "smart_memory.db"
        sm_store.init_memory_db()

    def tearDown(self) -> None:
        sm_store.DB_PATH = self._orig_db
        self._tmpdir.cleanup()

    def test_returns_string(self) -> None:
        self.assertIsInstance(sm_search.get_relevant_context("query"), str)

    def test_empty_db_returns_empty(self) -> None:
        result = sm_search.get_relevant_context("anything")
        self.assertEqual(result, "")

    def test_with_matching_memory_nonempty(self) -> None:
        sm_store.add_memory(text="Python is excellent for automation tasks and scripting")
        result = sm_search.get_relevant_context("Python automation")
        self.assertGreater(len(result), 0)

    def test_empty_query_returns_empty(self) -> None:
        result = sm_search.get_relevant_context("")
        self.assertEqual(result, "")


# ─────────────────────────────────────────────────────────────────────────────
# agent_registry/store — row_to_dict (pure)
# ─────────────────────────────────────────────────────────────────────────────

class AgentRegistryRowToDictTest(unittest.TestCase):
    def _row(self, **overrides) -> dict:
        base = {
            "id": "agent-1",
            "name": "Test",
            "capabilities_json": '["search", "code"]',
            "tags_json": '["alpha", "beta"]',
            "config_json": '{"icon": "★"}',
            "enabled": 1,
        }
        base.update(overrides)
        return base

    def test_returns_dict(self) -> None:
        self.assertIsInstance(ar_store.row_to_dict(self._row()), dict)

    def test_capabilities_parsed(self) -> None:
        result = ar_store.row_to_dict(self._row())
        self.assertEqual(result["capabilities"], ["search", "code"])

    def test_capabilities_json_removed(self) -> None:
        result = ar_store.row_to_dict(self._row())
        self.assertNotIn("capabilities_json", result)

    def test_tags_parsed(self) -> None:
        result = ar_store.row_to_dict(self._row())
        self.assertEqual(result["tags"], ["alpha", "beta"])

    def test_config_parsed(self) -> None:
        result = ar_store.row_to_dict(self._row())
        self.assertEqual(result["config"]["icon"], "★")

    def test_enabled_is_bool(self) -> None:
        result = ar_store.row_to_dict(self._row(enabled=1))
        self.assertIsInstance(result["enabled"], bool)

    def test_enabled_true_when_one(self) -> None:
        self.assertTrue(ar_store.row_to_dict(self._row(enabled=1))["enabled"])

    def test_enabled_false_when_zero(self) -> None:
        self.assertFalse(ar_store.row_to_dict(self._row(enabled=0))["enabled"])

    def test_invalid_capabilities_json_defaults_to_empty_list(self) -> None:
        result = ar_store.row_to_dict(self._row(capabilities_json="bad"))
        self.assertEqual(result["capabilities"], [])

    def test_invalid_config_json_defaults_to_empty_dict(self) -> None:
        result = ar_store.row_to_dict(self._row(config_json="bad"))
        self.assertEqual(result["config"], {})

    def test_preserves_other_keys(self) -> None:
        result = ar_store.row_to_dict(self._row())
        self.assertEqual(result["id"], "agent-1")
        self.assertEqual(result["name"], "Test")


# ─────────────────────────────────────────────────────────────────────────────
# run_history/store — load_legacy_runs (pure file-reader)
# ─────────────────────────────────────────────────────────────────────────────

class LoadLegacyRunsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write(self, name: str, content: object) -> Path:
        p = self._dir / name
        p.write_text(json.dumps(content), encoding="utf-8")
        return p

    def test_nonexistent_returns_empty(self) -> None:
        result = rh_store.load_legacy_runs(self._dir / "missing.json")
        self.assertEqual(result, [])

    def test_empty_list_returns_empty(self) -> None:
        p = self._write("empty.json", [])
        self.assertEqual(rh_store.load_legacy_runs(p), [])

    def test_list_format_returns_dicts(self) -> None:
        runs = [{"run_id": "r1", "ok": True}, {"run_id": "r2"}]
        p = self._write("runs.json", runs)
        result = rh_store.load_legacy_runs(p)
        self.assertEqual(len(result), 2)

    def test_list_skips_non_dicts(self) -> None:
        payload = [{"run_id": "r1"}, "not a dict", 42, None]
        p = self._write("mixed.json", payload)
        result = rh_store.load_legacy_runs(p)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["run_id"], "r1")

    def test_dict_format_extracts_values(self) -> None:
        payload = {"a": {"run_id": "r1"}, "b": {"run_id": "r2"}}
        p = self._write("dict_runs.json", payload)
        result = rh_store.load_legacy_runs(p)
        self.assertEqual(len(result), 2)

    def test_dict_format_skips_non_dict_values(self) -> None:
        payload = {"a": {"run_id": "r1"}, "b": "not a dict"}
        p = self._write("partial.json", payload)
        result = rh_store.load_legacy_runs(p)
        self.assertEqual(len(result), 1)

    def test_invalid_json_returns_empty(self) -> None:
        p = self._dir / "bad.json"
        p.write_text("not valid json!!!", encoding="utf-8")
        self.assertEqual(rh_store.load_legacy_runs(p), [])

    def test_returns_list_type(self) -> None:
        p = self._write("runs.json", [])
        self.assertIsInstance(rh_store.load_legacy_runs(p), list)

    def test_scalar_payload_returns_empty(self) -> None:
        p = self._write("scalar.json", 42)
        self.assertEqual(rh_store.load_legacy_runs(p), [])


if __name__ == "__main__":
    unittest.main()
