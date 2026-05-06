"""Tests for application/chat/prompting (wants_explicit_datetime_answer,
compose_human_style_rules, build_runtime_datetime_context) and
application/chat/timeline (append_timeline)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.prompting import (  # noqa: E402
    wants_explicit_datetime_answer,
    compose_human_style_rules,
    build_runtime_datetime_context,
)
from app.application.chat.timeline import append_timeline  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# wants_explicit_datetime_answer
# ─────────────────────────────────────────────────────────────────────────────

class WantsExplicitDatetimeAnswerTest(unittest.TestCase):
    def test_russian_date_phrase(self) -> None:
        self.assertTrue(wants_explicit_datetime_answer("какая сегодня дата"))

    def test_russian_time_phrase(self) -> None:
        self.assertTrue(wants_explicit_datetime_answer("который час"))

    def test_russian_number_today(self) -> None:
        self.assertTrue(wants_explicit_datetime_answer("какое сегодня число"))

    def test_russian_day_of_week(self) -> None:
        self.assertTrue(wants_explicit_datetime_answer("какой сегодня день недели"))

    def test_russian_current_time(self) -> None:
        self.assertTrue(wants_explicit_datetime_answer("сколько сейчас времени"))

    def test_english_what_time(self) -> None:
        self.assertTrue(wants_explicit_datetime_answer("what time is it"))

    def test_english_current_date(self) -> None:
        self.assertTrue(wants_explicit_datetime_answer("current date"))

    def test_english_todays_date(self) -> None:
        self.assertTrue(wants_explicit_datetime_answer("today's date"))

    def test_general_query_false(self) -> None:
        self.assertFalse(wants_explicit_datetime_answer("tell me about Python"))

    def test_empty_false(self) -> None:
        self.assertFalse(wants_explicit_datetime_answer(""))

    def test_none_false(self) -> None:
        self.assertFalse(wants_explicit_datetime_answer(None))  # type: ignore[arg-type]

    def test_returns_bool(self) -> None:
        self.assertIsInstance(wants_explicit_datetime_answer("test"), bool)

    def test_case_insensitive(self) -> None:
        self.assertTrue(wants_explicit_datetime_answer("What Time Is It"))

    def test_unrelated_time_topic_false(self) -> None:
        self.assertFalse(wants_explicit_datetime_answer("explain time complexity"))


# ─────────────────────────────────────────────────────────────────────────────
# compose_human_style_rules
# ─────────────────────────────────────────────────────────────────────────────

class ComposeHumanStyleRulesTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        result = compose_human_style_rules(None)
        self.assertIsInstance(result, str)

    def test_contains_final_answer_rules(self) -> None:
        result = compose_human_style_rules(None)
        self.assertIn("FINAL ANSWER RULES", result)

    def test_contains_temporal_mode(self) -> None:
        result = compose_human_style_rules({"mode": "hard"})
        self.assertIn("hard", result)

    def test_none_temporal_uses_defaults(self) -> None:
        result = compose_human_style_rules(None)
        self.assertIn("none", result)

    def test_contains_years_when_provided(self) -> None:
        result = compose_human_style_rules({"years": [2023, 2024]})
        self.assertIn("2023", result)
        self.assertIn("2024", result)

    def test_freshness_sensitive_reflected(self) -> None:
        result = compose_human_style_rules({"freshness_sensitive": True})
        self.assertIn("True", result)

    def test_reasoning_depth_reflected(self) -> None:
        result = compose_human_style_rules({"reasoning_depth": "deep"})
        self.assertIn("deep", result)

    def test_non_empty_output(self) -> None:
        result = compose_human_style_rules({})
        self.assertGreater(len(result), 50)

    def test_no_years_shows_none(self) -> None:
        result = compose_human_style_rules({"years": []})
        self.assertIn("none", result)


# ─────────────────────────────────────────────────────────────────────────────
# build_runtime_datetime_context
# ─────────────────────────────────────────────────────────────────────────────

class BuildRuntimeDatetimeContextTest(unittest.TestCase):
    def test_datetime_query_returns_nonempty(self) -> None:
        result = build_runtime_datetime_context("what time is it")
        self.assertGreater(len(result), 0)

    def test_datetime_query_returns_string(self) -> None:
        result = build_runtime_datetime_context("current date")
        self.assertIsInstance(result, str)

    def test_non_datetime_query_still_returns_string(self) -> None:
        result = build_runtime_datetime_context("explain Python")
        self.assertIsInstance(result, str)

    def test_empty_query_returns_string(self) -> None:
        result = build_runtime_datetime_context("")
        self.assertIsInstance(result, str)

    def test_datetime_context_contains_year(self) -> None:
        result = build_runtime_datetime_context("что сегодня за дата")
        # Should contain a 4-digit year
        import re
        self.assertTrue(re.search(r"\b20\d{2}\b", result))

    def test_datetime_context_contains_runtime_marker(self) -> None:
        result = build_runtime_datetime_context("какой сегодня день")
        # The context should have some structured label
        self.assertGreater(len(result), 10)


# ─────────────────────────────────────────────────────────────────────────────
# append_timeline
# ─────────────────────────────────────────────────────────────────────────────

class AppendTimelineTest(unittest.TestCase):
    def test_appends_to_list(self) -> None:
        tl: list = []
        append_timeline(tl, "step1", "Searching", "running", "...")
        self.assertEqual(len(tl), 1)

    def test_appended_item_has_step(self) -> None:
        tl: list = []
        append_timeline(tl, "s1", "Title", "done", "detail")
        self.assertEqual(tl[0]["step"], "s1")

    def test_appended_item_has_title(self) -> None:
        tl: list = []
        append_timeline(tl, "s1", "My Title", "done", "detail")
        self.assertEqual(tl[0]["title"], "My Title")

    def test_appended_item_has_status(self) -> None:
        tl: list = []
        append_timeline(tl, "s1", "T", "running", "d")
        self.assertEqual(tl[0]["status"], "running")

    def test_appended_item_has_detail(self) -> None:
        tl: list = []
        append_timeline(tl, "s1", "T", "done", "extra info")
        self.assertEqual(tl[0]["detail"], "extra info")

    def test_multiple_appends(self) -> None:
        tl: list = []
        append_timeline(tl, "s1", "T1", "done", "")
        append_timeline(tl, "s2", "T2", "error", "")
        self.assertEqual(len(tl), 2)
        self.assertEqual(tl[1]["step"], "s2")

    def test_returns_none(self) -> None:
        tl: list = []
        result = append_timeline(tl, "s", "t", "st", "d")
        self.assertIsNone(result)

    def test_appended_item_is_dict(self) -> None:
        tl: list = []
        append_timeline(tl, "s", "t", "st", "d")
        self.assertIsInstance(tl[0], dict)

    def test_item_has_exactly_four_keys(self) -> None:
        tl: list = []
        append_timeline(tl, "s", "t", "st", "d")
        self.assertEqual(set(tl[0].keys()), {"step", "title", "status", "detail"})


if __name__ == "__main__":
    unittest.main()
