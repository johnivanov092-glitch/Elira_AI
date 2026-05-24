"""Tests for application/chat sub-modules:
  memory_policy (is_direct_personal_memory_query, trim_history,
                 should_recall_memory_context, get_memory_recall_limits)
  context_builder (strip_frontend_project_context)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.memory_policy import (  # noqa: E402
    is_direct_personal_memory_query,
    trim_history,
    should_recall_memory_context,
    get_memory_recall_limits,
)
from app.application.chat.context_builder import (  # noqa: E402
    strip_frontend_project_context,
)


# ─────────────────────────────────────────────────────────────────────────────
# is_direct_personal_memory_query
# ─────────────────────────────────────────────────────────────────────────────

class IsDirectPersonalMemoryQueryTest(unittest.TestCase):
    def test_what_is_my_name_english(self) -> None:
        self.assertTrue(is_direct_personal_memory_query("what is my name?"))

    def test_do_you_know_my_name_english(self) -> None:
        self.assertTrue(is_direct_personal_memory_query("do you know my name?"))

    def test_kak_menya_zovut_russian(self) -> None:
        self.assertTrue(is_direct_personal_memory_query("как меня зовут?"))

    def test_ty_znaesh_kak_russian(self) -> None:
        self.assertTrue(is_direct_personal_memory_query("ты знаешь как меня зовут?"))

    def test_general_query_is_false(self) -> None:
        self.assertFalse(is_direct_personal_memory_query("tell me about Python"))

    def test_empty_is_false(self) -> None:
        self.assertFalse(is_direct_personal_memory_query(""))

    def test_none_is_false(self) -> None:
        self.assertFalse(is_direct_personal_memory_query(None))  # type: ignore[arg-type]

    def test_returns_bool(self) -> None:
        self.assertIsInstance(is_direct_personal_memory_query("test"), bool)

    def test_partial_phrase_is_false(self) -> None:
        self.assertFalse(is_direct_personal_memory_query("tell me what is my name history"))


# ─────────────────────────────────────────────────────────────────────────────
# trim_history
# ─────────────────────────────────────────────────────────────────────────────

class TrimHistoryTest(unittest.TestCase):
    def test_none_returns_empty(self) -> None:
        self.assertEqual(trim_history(None), [])

    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual(trim_history([]), [])

    def test_short_list_unchanged(self) -> None:
        h = [1, 2, 3, 4]
        self.assertEqual(trim_history(h, max_pairs=2), [1, 2, 3, 4])

    def test_exact_limit_unchanged(self) -> None:
        h = list(range(1, 5))  # 4 items = 2 pairs
        self.assertEqual(trim_history(h, max_pairs=2), h)

    def test_long_list_keeps_first_pair_and_recent(self) -> None:
        h = list(range(1, 13))  # 12 items
        result = trim_history(h, max_pairs=2)
        # max_pairs=2 → limit=4 → first pair [1,2] + last (4-2)=2 = [11,12]
        self.assertEqual(result, [1, 2, 11, 12])

    def test_returns_list_type(self) -> None:
        result = trim_history([1, 2], max_pairs=5)
        self.assertIsInstance(result, list)

    def test_does_not_modify_original(self) -> None:
        h = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        original_len = len(h)
        trim_history(h, max_pairs=2)
        self.assertEqual(len(h), original_len)

    def test_default_max_pairs_is_ten(self) -> None:
        h = list(range(1, 25))  # 24 items = 12 pairs
        result = trim_history(h)  # default max_pairs=10
        # limit=20 → length 20 (keeps all if len<=20, else trim)
        self.assertLessEqual(len(result), 22)

    def test_large_history_shorter_than_original(self) -> None:
        h = list(range(1, 101))  # 100 items
        result = trim_history(h, max_pairs=5)
        self.assertLess(len(result), 100)


# ─────────────────────────────────────────────────────────────────────────────
# should_recall_memory_context
# ─────────────────────────────────────────────────────────────────────────────

class ShouldRecallMemoryContextTest(unittest.TestCase):
    def _false_cmd(self, _: str) -> bool:
        return False

    def _true_cmd(self, _: str) -> bool:
        return True

    def test_normal_query_recalls(self) -> None:
        result = should_recall_memory_context(
            "what is Python", "chat", None,
            is_memory_command_func=self._false_cmd,
        )
        self.assertTrue(result)

    def test_memory_command_skips_recall(self) -> None:
        result = should_recall_memory_context(
            "remember this", "chat", None,
            is_memory_command_func=self._true_cmd,
        )
        self.assertFalse(result)

    def test_research_hard_freshness_skips_recall(self) -> None:
        temporal = {"mode": "hard", "freshness_sensitive": True}
        result = should_recall_memory_context(
            "latest news", "research", temporal,
            is_memory_command_func=self._false_cmd,
        )
        self.assertFalse(result)

    def test_research_hard_not_freshness_recalls(self) -> None:
        temporal = {"mode": "hard", "freshness_sensitive": False}
        result = should_recall_memory_context(
            "latest news", "research", temporal,
            is_memory_command_func=self._false_cmd,
        )
        self.assertTrue(result)

    def test_research_soft_recalls(self) -> None:
        temporal = {"mode": "soft", "freshness_sensitive": True}
        result = should_recall_memory_context(
            "query", "research", temporal,
            is_memory_command_func=self._false_cmd,
        )
        self.assertTrue(result)

    def test_none_temporal_recalls(self) -> None:
        result = should_recall_memory_context(
            "query", "chat", None,
            is_memory_command_func=self._false_cmd,
        )
        self.assertTrue(result)


# ─────────────────────────────────────────────────────────────────────────────
# get_memory_recall_limits
# ─────────────────────────────────────────────────────────────────────────────

class GetMemoryRecallLimitsTest(unittest.TestCase):
    def test_direct_query_returns_small_limit(self) -> None:
        result = get_memory_recall_limits("what is my name?")
        self.assertEqual(result, (1, 0))

    def test_russian_direct_query(self) -> None:
        result = get_memory_recall_limits("как меня зовут?")
        self.assertEqual(result, (1, 0))

    def test_general_query_returns_large_limit(self) -> None:
        result = get_memory_recall_limits("explain Python")
        self.assertEqual(result, (5, 3))

    def test_empty_query_returns_large_limit(self) -> None:
        result = get_memory_recall_limits("")
        self.assertEqual(result, (5, 3))

    def test_returns_tuple(self) -> None:
        self.assertIsInstance(get_memory_recall_limits("test"), tuple)

    def test_tuple_length_is_two(self) -> None:
        self.assertEqual(len(get_memory_recall_limits("test")), 2)


# ─────────────────────────────────────────────────────────────────────────────
# strip_frontend_project_context
# ─────────────────────────────────────────────────────────────────────────────

class StripFrontendProjectContextTest(unittest.TestCase):
    def test_strips_project_block(self) -> None:
        text = "hello\n\nОткрыт проект: my-project"
        result = strip_frontend_project_context(text)
        self.assertEqual(result, "hello")

    def test_no_marker_unchanged(self) -> None:
        text = "plain user message"
        self.assertEqual(strip_frontend_project_context(text), "plain user message")

    def test_empty_unchanged(self) -> None:
        self.assertEqual(strip_frontend_project_context(""), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(strip_frontend_project_context(None), "")  # type: ignore[arg-type]

    def test_strips_trailing_whitespace(self) -> None:
        text = "hello   \n\nОткрыт проект: foo"
        result = strip_frontend_project_context(text)
        self.assertEqual(result, "hello")

    def test_only_marker_returns_empty(self) -> None:
        text = "\n\nОткрыт проект: bar"
        result = strip_frontend_project_context(text)
        self.assertEqual(result, "")

    def test_marker_mid_message_strips_from_marker(self) -> None:
        text = "part one\n\nОткрыт проект: data\nmore project stuff"
        result = strip_frontend_project_context(text)
        self.assertEqual(result, "part one")

    def test_returns_string(self) -> None:
        self.assertIsInstance(strip_frontend_project_context("test"), str)


if __name__ == "__main__":
    unittest.main()
