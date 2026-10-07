"""Tests for pure helpers across two modules.

  application/web_query_planner/runtime.py — _build_search_query
  application/response_cache/runtime.py   — _normalize_query, _query_hash

All functions are pure (no DB writes exercised; module-level bootstrap is
idempotent and creates local SQLite files only).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.web_query_planner.runtime import (  # noqa: E402
    _build_search_query,
)
from app.application.response_cache.runtime import (  # noqa: E402
    _normalize_query,
    _query_hash,
)


# ─────────────────────────────────────────────────────────────────────────────
# web_query_planner/runtime.py — _build_search_query
# ─────────────────────────────────────────────────────────────────────────────

class BuildSearchQueryTest(unittest.TestCase):

    _GEO_KZ = {
        "city": "Алматы",
        "country": "Казахстан",
        "label": "Алматы, Казахстан",
        "scope": "алматы",
    }
    _GEO_EMPTY = {"city": "", "country": "", "label": "", "scope": ""}

    def _call(self, segment: str, intent: str, geo=None, time_window: str = "") -> str:
        if geo is None:
            geo = self._GEO_EMPTY
        return _build_search_query(segment, intent, geo, time_window)

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_string(self) -> None:
        self.assertIsInstance(self._call("hello", "general_web"), str)

    def test_nonempty_for_all_intents(self) -> None:
        for intent in ("finance", "geo_news", "general_news",
                       "price_rate", "status_current", "historical", "general_web"):
            result = self._call("test query", intent, self._GEO_EMPTY)
            self.assertGreater(len(result), 0, f"Empty result for intent={intent}")

    # ── intent-specific behaviour ─────────────────────────────────────────────

    def test_general_web_returns_stripped_segment(self) -> None:
        result = self._call("  how python works  ", "general_web")
        self.assertEqual(result, "how python works")

    def test_historical_returns_stripped_segment(self) -> None:
        result = self._call("  history of science  ", "historical")
        self.assertEqual(result, "history of science")

    def test_geo_news_result_is_string(self) -> None:
        result = self._call("происшествия", "geo_news", self._GEO_KZ, "сегодня")
        self.assertIsInstance(result, str)

    def test_finance_intent_returns_string(self) -> None:
        result = self._call("курс доллара", "finance", self._GEO_KZ, "")
        self.assertIsInstance(result, str)

    def test_general_news_returns_string(self) -> None:
        result = self._call("latest developments", "general_news", self._GEO_EMPTY, "")
        self.assertIsInstance(result, str)

    def test_price_rate_returns_string(self) -> None:
        result = self._call("цены на нефть", "price_rate", self._GEO_EMPTY, "на сегодня")
        self.assertIsInstance(result, str)

    def test_status_current_returns_string(self) -> None:
        result = self._call("что происходит", "status_current", self._GEO_EMPTY, "сейчас")
        self.assertIsInstance(result, str)

    def test_unknown_intent_falls_through_to_segment(self) -> None:
        # No branch matches → falls through to `return segment.strip()`
        result = self._call("some query", "unknown_intent_xyz")
        self.assertEqual(result, "some query")


# ─────────────────────────────────────────────────────────────────────────────
# response_cache/runtime.py — _normalize_query, _query_hash
# ─────────────────────────────────────────────────────────────────────────────

class NormalizeQueryTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(_normalize_query("hello"), str)

    def test_lowercased(self) -> None:
        self.assertEqual(_normalize_query("HELLO WORLD"), "hello world")

    def test_stripped(self) -> None:
        self.assertEqual(_normalize_query("  hello  "), "hello")

    def test_punctuation_removed(self) -> None:
        result = _normalize_query("hello, world!")
        self.assertNotIn(",", result)
        self.assertNotIn("!", result)

    def test_whitespace_collapsed(self) -> None:
        result = _normalize_query("hello   world")
        self.assertEqual(result, "hello world")

    def test_empty_string(self) -> None:
        self.assertEqual(_normalize_query(""), "")

    def test_idempotent(self) -> None:
        text = "hello world"
        self.assertEqual(_normalize_query(text), _normalize_query(_normalize_query(text)))


class QueryHashTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(_query_hash("hello", "llama3", "analyst"), str)

    def test_returns_64_char_hex(self) -> None:
        h = _query_hash("hello", "llama3", "analyst")
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_deterministic(self) -> None:
        h1 = _query_hash("hello world", "gpt4", "dev")
        h2 = _query_hash("hello world", "gpt4", "dev")
        self.assertEqual(h1, h2)

    def test_different_queries_different_hashes(self) -> None:
        h1 = _query_hash("query one", "model", "profile")
        h2 = _query_hash("query two", "model", "profile")
        self.assertNotEqual(h1, h2)

    def test_different_models_different_hashes(self) -> None:
        h1 = _query_hash("query", "modelA", "profile")
        h2 = _query_hash("query", "modelB", "profile")
        self.assertNotEqual(h1, h2)

    def test_different_profiles_different_hashes(self) -> None:
        h1 = _query_hash("query", "model", "profileA")
        h2 = _query_hash("query", "model", "profileB")
        self.assertNotEqual(h1, h2)

    def test_empty_inputs_produces_hash(self) -> None:
        h = _query_hash("", "", "")
        self.assertEqual(len(h), 64)

if __name__ == "__main__":
    unittest.main()
