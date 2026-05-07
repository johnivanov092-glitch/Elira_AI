"""Tests for pure helpers across two modules.

  application/web_query_planner/runtime.py — _should_merge,
                                              _augment_current_query,
                                              _build_subquery,
                                              _default_single_query
  application/code_agent/python_lab.py     — ok_check

All functions are pure (no DB, no HTTP, no FS side-effects).
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
    _should_merge,
    _augment_current_query,
    _build_subquery,
    _default_single_query,
)
from app.application.code_agent.python_lab import ok_check  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# web_query_planner/runtime.py — _should_merge
# ─────────────────────────────────────────────────────────────────────────────

class ShouldMergeTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(_should_merge({"intent_kind": "finance"}, {"intent_kind": "finance"}), bool)

    def test_different_intent_kinds_false(self) -> None:
        a = {"intent_kind": "finance"}
        b = {"intent_kind": "general_news"}
        self.assertFalse(_should_merge(a, b))

    def test_finance_plus_finance_true(self) -> None:
        a = {"intent_kind": "finance"}
        b = {"intent_kind": "finance"}
        self.assertTrue(_should_merge(a, b))

    def test_price_rate_same_geo_true(self) -> None:
        a = {"intent_kind": "price_rate", "geo_scope": "kz"}
        b = {"intent_kind": "price_rate", "geo_scope": "kz"}
        self.assertTrue(_should_merge(a, b))

    def test_price_rate_different_geo_false(self) -> None:
        a = {"intent_kind": "price_rate", "geo_scope": "kz"}
        b = {"intent_kind": "price_rate", "geo_scope": "ru"}
        self.assertFalse(_should_merge(a, b))

    def test_general_news_plus_general_news_false(self) -> None:
        # general_news is not in the merge conditions
        a = {"intent_kind": "general_news"}
        b = {"intent_kind": "general_news"}
        self.assertFalse(_should_merge(a, b))

    def test_status_current_not_merged(self) -> None:
        a = {"intent_kind": "status_current"}
        b = {"intent_kind": "status_current"}
        self.assertFalse(_should_merge(a, b))

    def test_geo_news_not_merged_with_itself(self) -> None:
        a = {"intent_kind": "geo_news"}
        b = {"intent_kind": "geo_news"}
        self.assertFalse(_should_merge(a, b))


# ─────────────────────────────────────────────────────────────────────────────
# web_query_planner/runtime.py — _augment_current_query
# ─────────────────────────────────────────────────────────────────────────────

class AugmentCurrentQueryTest(unittest.TestCase):

    def _geo(self, city="", country="", label="") -> dict:
        return {"city": city, "country": country, "label": label}

    def test_returns_string(self) -> None:
        self.assertIsInstance(_augment_current_query("hello", {}, "", ""), str)

    def test_no_geo_no_time_unchanged(self) -> None:
        result = _augment_current_query("hello world", {}, "", "")
        self.assertEqual(result, "hello world")

    def test_geo_label_appended_when_not_in_query(self) -> None:
        geo = self._geo(city="almaty", country="kazakhstan", label="Almaty")
        result = _augment_current_query("news today", geo, "", "")
        self.assertIn("Almaty", result)

    def test_geo_label_not_appended_when_city_in_query(self) -> None:
        geo = self._geo(city="almaty", country="kazakhstan", label="Almaty")
        result = _augment_current_query("almaty news", geo, "", "")
        # city already present → no double-append
        self.assertEqual(result.count("Almaty"), 0)
        self.assertEqual(result.count("almaty"), 1)

    def test_geo_label_not_appended_when_country_in_query(self) -> None:
        geo = self._geo(city="almaty", country="kazakhstan", label="Almaty")
        result = _augment_current_query("kazakhstan news", geo, "", "")
        self.assertNotIn("Almaty", result)

    def test_time_window_appended_when_not_in_query(self) -> None:
        result = _augment_current_query("latest news", {}, "2024", "")
        self.assertIn("2024", result)

    def test_time_window_not_appended_when_already_present(self) -> None:
        result = _augment_current_query("news 2024", {}, "2024", "")
        self.assertEqual(result.count("2024"), 1)

    def test_empty_segment_returns_empty(self) -> None:
        result = _augment_current_query("", {}, "", "")
        self.assertEqual(result, "")

    def test_result_stripped(self) -> None:
        result = _augment_current_query("  news  ", {}, "", "")
        self.assertFalse(result.startswith(" "))
        self.assertFalse(result.endswith(" "))

    def test_geo_and_time_both_appended(self) -> None:
        geo = self._geo(city="paris", country="france", label="Paris")
        result = _augment_current_query("news", geo, "2024", "")
        self.assertIn("Paris", result)
        self.assertIn("2024", result)


# ─────────────────────────────────────────────────────────────────────────────
# web_query_planner/runtime.py — _build_subquery
# ─────────────────────────────────────────────────────────────────────────────

class BuildSubqueryTest(unittest.TestCase):

    def _call(self, segment="test query", **kwargs) -> dict:
        defaults = dict(temporal={}, overall_geo={}, time_window="", original_index=0)
        defaults.update(kwargs)
        return _build_subquery(segment, **defaults)

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._call(), dict)

    def test_required_keys_present(self) -> None:
        result = self._call()
        for key in ("query", "intent_kind", "freshness_class", "geo_scope",
                    "label", "priority", "local_first", "needs_deep_search",
                    "needs_news_feed", "preferred_domains"):
            self.assertIn(key, result)

    def test_query_contains_segment(self) -> None:
        result = self._call(segment="python tutorial")
        self.assertIn("python", result["query"])

    def test_general_segment_gets_general_web_intent(self) -> None:
        result = self._call(segment="how to code")
        self.assertEqual(result["intent_kind"], "general_web")

    def test_original_index_stored(self) -> None:
        result = self._call(segment="test", original_index=3)
        self.assertEqual(result["_original_index"], 3)

    def test_segment_stored(self) -> None:
        result = self._call(segment="my segment")
        self.assertEqual(result["_segment"], "my segment")

    def test_preferred_domains_is_list(self) -> None:
        result = self._call()
        self.assertIsInstance(result["preferred_domains"], list)

    def test_different_indices_different_original_index(self) -> None:
        r0 = self._call(segment="s", original_index=0)
        r1 = self._call(segment="s", original_index=5)
        self.assertEqual(r0["_original_index"], 0)
        self.assertEqual(r1["_original_index"], 5)


# ─────────────────────────────────────────────────────────────────────────────
# web_query_planner/runtime.py — _default_single_query
# ─────────────────────────────────────────────────────────────────────────────

class DefaultSingleQueryTest(unittest.TestCase):

    def _call(self, query="test query", **kwargs) -> dict:
        defaults = dict(temporal={}, geo={}, time_window="")
        defaults.update(kwargs)
        return _default_single_query(query, **defaults)

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._call(), dict)

    def test_is_multi_intent_false(self) -> None:
        self.assertFalse(self._call()["is_multi_intent"])

    def test_total_subqueries_is_1(self) -> None:
        self.assertEqual(self._call()["total_subqueries"], 1)

    def test_pass_count_is_1(self) -> None:
        self.assertEqual(self._call()["pass_count"], 1)

    def test_overflow_applied_false(self) -> None:
        self.assertFalse(self._call()["overflow_applied"])

    def test_subqueries_has_one_item(self) -> None:
        result = self._call()
        self.assertEqual(len(result["subqueries"]), 1)

    def test_uncovered_subqueries_empty(self) -> None:
        result = self._call()
        self.assertEqual(result["uncovered_subqueries"], [])

    def test_passes_has_one_item(self) -> None:
        result = self._call()
        self.assertEqual(len(result["passes"]), 1)

    def test_required_keys_present(self) -> None:
        result = self._call()
        for key in ("is_multi_intent", "total_subqueries", "pass_count",
                    "overflow_applied", "subqueries", "uncovered_subqueries",
                    "passes", "freshness_class", "geo_scope"):
            self.assertIn(key, result)

    def test_no_internal_segment_key(self) -> None:
        # _segment key should be popped before returning
        result = self._call()
        self.assertNotIn("_segment", result)

    def test_no_internal_index_key(self) -> None:
        # _original_index key should be popped before returning
        result = self._call()
        self.assertNotIn("_original_index", result)


# ─────────────────────────────────────────────────────────────────────────────
# code_agent/python_lab.py — ok_check
# ─────────────────────────────────────────────────────────────────────────────

class OkCheckTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(ok_check("", "", 0), bool)

    def test_nonzero_returncode_false(self) -> None:
        self.assertFalse(ok_check("output", "", 1))

    def test_nonzero_returncode_2_false(self) -> None:
        self.assertFalse(ok_check("output", "", 2))

    def test_zero_returncode_no_errors_true(self) -> None:
        self.assertTrue(ok_check("Hello, World!", "", 0))

    def test_empty_stderr_true(self) -> None:
        self.assertTrue(ok_check("output", "", 0))

    def test_traceback_in_stderr_false(self) -> None:
        stderr = "Traceback (most recent call last):\n  File...\nValueError: oops"
        self.assertFalse(ok_check("", stderr, 0))

    def test_valueerror_line_false(self) -> None:
        # Line matching ^\\w*Error: pattern → not ok
        self.assertFalse(ok_check("", "ValueError: invalid", 0))

    def test_typeerror_line_false(self) -> None:
        self.assertFalse(ok_check("", "TypeError: bad arg", 0))

    def test_warning_only_true(self) -> None:
        # UserWarning does not match ^\w*Error: → still ok
        self.assertTrue(ok_check("output", "UserWarning: deprecated", 0))

    def test_deprecation_warning_true(self) -> None:
        self.assertTrue(ok_check("output", "DeprecationWarning: use X instead", 0))

    def test_empty_stdout_and_stderr_true(self) -> None:
        self.assertTrue(ok_check("", "", 0))

    def test_error_in_middle_of_stderr_false(self) -> None:
        stderr = "some output\nRuntimeError: unexpected\nmore output"
        self.assertFalse(ok_check("", stderr, 0))

    def test_nonzero_with_warning_still_false(self) -> None:
        self.assertFalse(ok_check("output", "UserWarning: x", 1))


if __name__ == "__main__":
    unittest.main()
