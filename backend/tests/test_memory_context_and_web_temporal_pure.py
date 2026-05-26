"""Tests for pure helpers across three previously zero-covered modules.

  infrastructure/search/web_temporal.py - compute_freshness
  infrastructure/search/web_query.py   - is_strict_web_only_query
  application/memory/context.py        - default_content_hash,
                                         _memory_type_weight,
                                         _clean_memory_text,
                                         _memory_query_words

All functions are pure (no DB, no HTTP, no FS).

Note: tests for `core.memory._content_hash` were removed when that
facade module was deleted (it just re-exported `default_content_hash`
from `application/memory/context.py`, which is still tested below).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.infrastructure.search.web_temporal import compute_freshness  # noqa: E402
from app.infrastructure.search.web_query import is_strict_web_only_query  # noqa: E402
from app.application.memory.context import (  # noqa: E402
    default_content_hash,
    _memory_type_weight,
    _clean_memory_text,
    _memory_query_words,
)


# infrastructure/search/web_temporal.py - compute_freshness

class ComputeFreshnessTest(unittest.TestCase):
    def _call(self, temporal=None, *, has_current_evidence=False,
               news_count=0, fetched_pages=0, deeper_search=False):
        return compute_freshness(
            temporal=temporal or {},
            has_current_evidence=has_current_evidence,
            news_count=news_count,
            fetched_pages=fetched_pages,
            deeper_search=deeper_search,
        )

    def test_returns_tuple(self) -> None:
        self.assertIsInstance(self._call(), tuple)

    def test_tuple_has_two_elements(self) -> None:
        self.assertEqual(len(self._call()), 2)

    def test_both_elements_are_strings(self) -> None:
        state, note = self._call()
        self.assertIsInstance(state, str)
        self.assertIsInstance(note, str)

    # freshness_sensitive + good evidence -> fresh_checked
    def test_freshness_sensitive_with_news_fresh_checked(self) -> None:
        state, note = self._call(
            {"freshness_sensitive": True}, has_current_evidence=True, news_count=2
        )
        self.assertEqual(state, "fresh_checked")

    def test_freshness_sensitive_fresh_note_content(self) -> None:
        _, note = self._call(
            {"freshness_sensitive": True}, has_current_evidence=True, news_count=1
        )
        self.assertIn("fresh_checked", note)

    def test_freshness_sensitive_with_fetched_pages_fresh(self) -> None:
        state, _ = self._call(
            {"freshness_sensitive": True}, has_current_evidence=True, fetched_pages=2
        )
        self.assertEqual(state, "fresh_checked")

    def test_freshness_sensitive_with_deeper_search_fresh(self) -> None:
        state, _ = self._call(
            {"freshness_sensitive": True}, has_current_evidence=True, deeper_search=True
        )
        self.assertEqual(state, "fresh_checked")

    # freshness_sensitive + no evidence -> unverified_current
    def test_freshness_sensitive_no_evidence_unverified(self) -> None:
        state, _ = self._call({"freshness_sensitive": True})
        self.assertEqual(state, "unverified_current")

    def test_unverified_note_content(self) -> None:
        _, note = self._call({"freshness_sensitive": True})
        self.assertIn("unverified_current", note)

    # stable_historical branch
    def test_stable_historical_state(self) -> None:
        state, _ = self._call({"stable_historical": True})
        self.assertEqual(state, "historical_or_stable")

    def test_stable_historical_note_content(self) -> None:
        _, note = self._call({"stable_historical": True})
        self.assertIn("historical_or_stable", note)

    # default branch
    def test_default_temporal_standard_web(self) -> None:
        state, _ = self._call({})
        self.assertEqual(state, "standard_web")

    def test_standard_web_note_content(self) -> None:
        _, note = self._call({})
        self.assertIn("standard_web", note)

    def test_empty_temporal_standard_web(self) -> None:
        state, _ = compute_freshness(
            temporal={},
            has_current_evidence=False,
            news_count=0,
            fetched_pages=0,
            deeper_search=False,
        )
        self.assertEqual(state, "standard_web")


# infrastructure/search/web_query.py - is_strict_web_only_query

class IsStrictWebOnlyQueryTest(unittest.TestCase):
    def test_returns_bool(self) -> None:
        self.assertIsInstance(is_strict_web_only_query("hello"), bool)

    def test_weather_true(self) -> None:
        self.assertTrue(is_strict_web_only_query("weather in Almaty"))

    def test_today_true(self) -> None:
        self.assertTrue(is_strict_web_only_query("news today"))

    def test_current_true(self) -> None:
        self.assertTrue(is_strict_web_only_query("current bitcoin price"))

    def test_latest_true(self) -> None:
        self.assertTrue(is_strict_web_only_query("latest updates"))

    def test_usd_true(self) -> None:
        self.assertTrue(is_strict_web_only_query("usd exchange rate"))

    def test_eur_true(self) -> None:
        self.assertTrue(is_strict_web_only_query("eur/kzt conversion"))

    def test_news_true(self) -> None:
        self.assertTrue(is_strict_web_only_query("news from Kazakhstan"))

    def test_general_query_false(self) -> None:
        self.assertFalse(is_strict_web_only_query("how does python work"))

    def test_empty_string_false(self) -> None:
        self.assertFalse(is_strict_web_only_query(""))

    def test_none_false(self) -> None:
        self.assertFalse(is_strict_web_only_query(None))  # type: ignore[arg-type]

    def test_case_insensitive(self) -> None:
        self.assertTrue(is_strict_web_only_query("WEATHER forecast"))

    def test_mixed_case_today(self) -> None:
        self.assertTrue(is_strict_web_only_query("Today in tech"))


# application/memory/context.py - default_content_hash

class DefaultContentHashTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(default_content_hash("hello"), str)

    def test_32_hex_chars(self) -> None:
        self.assertEqual(len(default_content_hash("hello")), 32)

    def test_deterministic(self) -> None:
        self.assertEqual(default_content_hash("foo"), default_content_hash("foo"))

    def test_different_texts_different_hashes(self) -> None:
        self.assertNotEqual(default_content_hash("foo"), default_content_hash("bar"))

    def test_strips_whitespace_and_lowercases(self) -> None:
        # "Hello" stripped/lowercased = "hello"
        self.assertEqual(default_content_hash("  HELLO  "), default_content_hash("hello"))

    def test_empty_string_produces_hash(self) -> None:
        result = default_content_hash("")
        self.assertEqual(len(result), 32)


# application/memory/context.py - _memory_type_weight

class MemoryTypeWeightTest(unittest.TestCase):
    def test_returns_float(self) -> None:
        self.assertIsInstance(_memory_type_weight("profile"), float)

    def test_profile_highest(self) -> None:
        self.assertGreater(_memory_type_weight("profile"), _memory_type_weight("chat"))

    def test_profile_weight(self) -> None:
        self.assertAlmostEqual(_memory_type_weight("profile"), 4.2)

    def test_chat_weight(self) -> None:
        self.assertAlmostEqual(_memory_type_weight("chat"), 1.0)

    def test_unknown_type_fallback(self) -> None:
        self.assertAlmostEqual(_memory_type_weight("nonexistent_type"), 1.2)

    def test_pinned_flag_adds_bonus(self) -> None:
        base = _memory_type_weight("chat", pinned=False)
        boosted = _memory_type_weight("chat", pinned=True)
        self.assertGreater(boosted, base)
        self.assertAlmostEqual(boosted - base, 1.2)

    def test_manual_source_adds_bonus(self) -> None:
        base = _memory_type_weight("chat", source="")
        boosted = _memory_type_weight("chat", source="manual")
        self.assertAlmostEqual(boosted - base, 0.3)

    def test_insight_weight_above_chat(self) -> None:
        self.assertGreater(_memory_type_weight("insight"), _memory_type_weight("chat"))

    def test_pinned_type_weight(self) -> None:
        self.assertAlmostEqual(_memory_type_weight("pinned"), 4.0)

    def test_summary_weight(self) -> None:
        self.assertAlmostEqual(_memory_type_weight("summary"), 2.9)

    def test_all_positive(self) -> None:
        for mt in ("profile", "pinned", "insight", "summary", "file", "chat", "general"):
            self.assertGreater(_memory_type_weight(mt), 0)


# application/memory/context.py - _clean_memory_text

class CleanMemoryTextTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(_clean_memory_text("hello"), str)

    def test_short_text_unchanged(self) -> None:
        self.assertEqual(_clean_memory_text("hello world"), "hello world")

    def test_strips_leading_trailing_whitespace(self) -> None:
        self.assertEqual(_clean_memory_text("  hello  "), "hello")

    def test_collapses_multiple_spaces(self) -> None:
        result = _clean_memory_text("hello   world")
        self.assertNotIn("   ", result)
        self.assertIn("hello world", result)

    def test_long_text_truncated(self) -> None:
        long = "A " * 500
        result = _clean_memory_text(long, max_chars=50)
        self.assertLessEqual(len(result), 55)  # 50 + ellipsis

    def test_truncated_ends_with_ellipsis(self) -> None:
        long = "B" * 1000
        result = _clean_memory_text(long, max_chars=50)
        self.assertIn("\u2026", result)

    def test_empty_string_empty_result(self) -> None:
        self.assertEqual(_clean_memory_text(""), "")

    def test_none_treated_as_empty(self) -> None:
        self.assertEqual(_clean_memory_text(None), "")  # type: ignore[arg-type]

    def test_exact_limit_not_truncated(self) -> None:
        text = "A" * 900
        result = _clean_memory_text(text)
        self.assertNotIn("\u2026", result)


# application/memory/context.py - _memory_query_words

class MemoryQueryWordsTest(unittest.TestCase):
    def test_returns_list(self) -> None:
        self.assertIsInstance(_memory_query_words("hello"), list)

    def test_short_words_excluded(self) -> None:
        # "ab" is < 3 chars
        result = _memory_query_words("ab cd python")
        self.assertNotIn("ab", result)
        self.assertNotIn("cd", result)

    def test_long_words_included(self) -> None:
        result = _memory_query_words("python testing framework")
        self.assertIn("python", result)
        self.assertIn("testing", result)
        self.assertIn("framework", result)

    def test_empty_string_empty_list(self) -> None:
        self.assertEqual(_memory_query_words(""), [])

    def test_none_empty_list(self) -> None:
        self.assertEqual(_memory_query_words(None), [])  # type: ignore[arg-type]

    def test_returns_lowercase(self) -> None:
        result = _memory_query_words("HELLO WORLD PYTHON")
        self.assertIn("hello", result)
        self.assertIn("world", result)
        self.assertIn("python", result)

    def test_minimum_length_three_chars_included(self) -> None:
        result = _memory_query_words("abc")
        self.assertIn("abc", result)

    def test_minimum_length_two_chars_excluded(self) -> None:
        result = _memory_query_words("ab")
        self.assertNotIn("ab", result)


if __name__ == "__main__":
    unittest.main()
