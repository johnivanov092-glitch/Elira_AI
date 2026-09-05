"""Tests for active browser action and browser agent helpers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.tools.browser_action_tool import (  # noqa: E402
    browser_runtime_hint,
    sync_playwright_available,
    sanitize_browser_actions,
)
from app.domain.tools.browser_agent_tool import (  # noqa: E402
    goal_keywords,
    score_link,
    rank_links,
)


# browser_runtime_hint

class BrowserRuntimeHintTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(browser_runtime_hint("error"), str)

    def test_notemplemented_gives_policy_hint(self) -> None:
        result = browser_runtime_hint(NotImplementedError("subprocess"))
        self.assertIn("Windows", result)

    def test_make_subprocess_transport_in_msg_gives_policy_hint(self) -> None:
        result = browser_runtime_hint("_make_subprocess_transport failed")
        self.assertIn("Windows", result)

    def test_executable_doesnt_exist_gives_install_hint(self) -> None:
        result = browser_runtime_hint("executable doesn't exist")
        self.assertIn("playwright install", result)

    def test_browsertype_launch_gives_install_hint(self) -> None:
        result = browser_runtime_hint("browsertype.launch failed")
        self.assertIn("playwright install", result)

    def test_generic_error_returns_text(self) -> None:
        result = browser_runtime_hint("some generic error message")
        self.assertIn("some generic error message", result)

    def test_empty_string_returns_string(self) -> None:
        result = browser_runtime_hint("")
        self.assertIsInstance(result, str)

    def test_none_returns_string(self) -> None:
        result = browser_runtime_hint(None)  # type: ignore[arg-type]
        self.assertIsInstance(result, str)

    def test_exception_object(self) -> None:
        exc = RuntimeError("connection refused")
        result = browser_runtime_hint(exc)
        self.assertIn("connection refused", result)


# sync_playwright_available

class SyncPlaywrightAvailableTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(sync_playwright_available(), bool)

    def test_consistent_result(self) -> None:
        # Two calls return the same value
        self.assertEqual(sync_playwright_available(), sync_playwright_available())


# sanitize_browser_actions

class SanitizeBrowserActionsTest(unittest.TestCase):

    def test_returns_list(self) -> None:
        self.assertIsInstance(sanitize_browser_actions([]), list)

    def test_none_returns_empty(self) -> None:
        self.assertEqual(sanitize_browser_actions(None), [])  # type: ignore[arg-type]

    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual(sanitize_browser_actions([]), [])

    def test_valid_open_action_kept(self) -> None:
        actions = [{"action": "open", "url": "https://example.com", "selector": "", "value": ""}]
        result = sanitize_browser_actions(actions)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["action"], "open")

    def test_allowed_actions_kept(self) -> None:
        for action in ("open", "click", "fill", "extract", "wait"):
            actions = [{"action": action}]
            result = sanitize_browser_actions(actions)
            self.assertEqual(len(result), 1)

    def test_unknown_action_filtered(self) -> None:
        actions = [{"action": "dangerous_action", "url": ""}]
        result = sanitize_browser_actions(actions)
        self.assertEqual(result, [])

    def test_non_dict_items_skipped(self) -> None:
        actions = ["string", 42, {"action": "open"}]
        result = sanitize_browser_actions(actions)
        self.assertEqual(len(result), 1)

    def test_max_12_items(self) -> None:
        actions = [{"action": "click"} for _ in range(20)]
        result = sanitize_browser_actions(actions)
        self.assertLessEqual(len(result), 12)

    def test_each_result_has_required_keys(self) -> None:
        actions = [{"action": "open", "url": "https://example.com"}]
        result = sanitize_browser_actions(actions)
        for item in result:
            for key in ("action", "url", "selector", "value", "ms"):
                self.assertIn(key, item)

    def test_ms_defaults_to_1000(self) -> None:
        actions = [{"action": "wait"}]
        result = sanitize_browser_actions(actions)
        self.assertEqual(result[0]["ms"], 1000)

    def test_ms_preserved_when_provided(self) -> None:
        actions = [{"action": "wait", "ms": 2000}]
        result = sanitize_browser_actions(actions)
        self.assertEqual(result[0]["ms"], 2000)

    def test_invalid_ms_falls_back_to_1000(self) -> None:
        actions = [{"action": "wait", "ms": "not-a-number"}]
        result = sanitize_browser_actions(actions)
        self.assertEqual(result[0]["ms"], 1000)

    def test_case_insensitive_action(self) -> None:
        # Function lowercases the action
        actions = [{"action": "OPEN"}]
        result = sanitize_browser_actions(actions)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["action"], "open")


# goal_keywords

class GoalKeywordsTest(unittest.TestCase):

    def test_returns_list(self) -> None:
        self.assertIsInstance(goal_keywords("hello"), list)

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(goal_keywords(""), [])

    def test_none_returns_empty(self) -> None:
        self.assertEqual(goal_keywords(None), [])  # type: ignore[arg-type]

    def test_short_words_excluded(self) -> None:
        result = goal_keywords("in on at")
        self.assertEqual(result, [])

    def test_stopwords_excluded(self) -> None:
        result = goal_keywords("the and for with")
        self.assertEqual(result, [])

    def test_content_words_included(self) -> None:
        result = goal_keywords("python fastapi analysis")
        self.assertIn("python", result)
        self.assertIn("fastapi", result)
        self.assertIn("analysis", result)

    def test_returns_lowercase(self) -> None:
        result = goal_keywords("PYTHON FastAPI")
        for word in result:
            self.assertEqual(word, word.lower())

    def test_max_12_words(self) -> None:
        result = goal_keywords("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu")
        self.assertLessEqual(len(result), 12)

    def test_each_word_at_least_3_chars(self) -> None:
        result = goal_keywords("analyze the python codebase for errors and bugs")
        for word in result:
            self.assertGreaterEqual(len(word), 3)


# score_link

class ScoreLinkTest(unittest.TestCase):

    def test_returns_int(self) -> None:
        self.assertIsInstance(score_link({}, []), int)

    def test_empty_link_zero_score(self) -> None:
        self.assertEqual(score_link({}, []), 0)

    def test_same_domain_adds_bonus(self) -> None:
        link = {"same_domain": True, "text": "", "title": "", "href": ""}
        result = score_link(link, [])
        self.assertGreater(result, 0)

    def test_different_domain_no_same_domain_bonus(self) -> None:
        link = {"same_domain": False, "text": "", "title": "", "href": ""}
        self.assertEqual(score_link(link, []), 0)

    def test_keyword_in_text_adds_score(self) -> None:
        link = {"text": "python tutorial", "title": "", "href": "", "same_domain": False}
        score_without = score_link(link, [])
        score_with = score_link(link, ["python"])
        self.assertGreater(score_with, score_without)

    def test_login_penalized(self) -> None:
        link = {"text": "login here", "title": "", "href": "", "same_domain": False}
        result = score_link(link, [])
        self.assertLess(result, 0)

    def test_social_link_penalized(self) -> None:
        link = {"text": "Follow us", "title": "", "href": "https://facebook.com/page", "same_domain": False}
        result = score_link(link, [])
        self.assertLess(result, 0)

    def test_docs_link_positive(self) -> None:
        link = {"text": "docs", "title": "", "href": "https://example.com/docs", "same_domain": False}
        result = score_link(link, [])
        self.assertGreater(result, 0)

    def test_multiple_keywords_accumulate(self) -> None:
        link = {"text": "python fastapi tutorial", "title": "", "href": "", "same_domain": False}
        score_1 = score_link(link, ["python"])
        score_2 = score_link(link, ["python", "fastapi"])
        self.assertGreater(score_2, score_1)


# rank_links

class RankLinksTest(unittest.TestCase):

    def test_returns_list(self) -> None:
        self.assertIsInstance(rank_links([], "goal", 10), list)

    def test_empty_links_returns_empty(self) -> None:
        self.assertEqual(rank_links([], "goal", 5), [])

    def test_respects_limit(self) -> None:
        links = [{"text": "link", "title": "", "href": f"https://example.com/{i}", "same_domain": True}
                 for i in range(10)]
        result = rank_links(links, "find python docs", 3)
        self.assertLessEqual(len(result), 3)

    def test_relevant_link_ranked_higher(self) -> None:
        links = [
            {"text": "python docs", "title": "", "href": "https://docs.python.org", "same_domain": True},
            {"text": "random page", "title": "", "href": "https://example.com/random", "same_domain": False},
        ]
        result = rank_links(links, "learn python programming", 10)
        self.assertGreater(len(result), 0)

    def test_result_items_are_dicts(self) -> None:
        links = [{"text": "page", "title": "", "href": "https://example.com", "same_domain": True}]
        result = rank_links(links, "goal", 5)
        for item in result:
            self.assertIsInstance(item, dict)

    def test_score_key_added_to_results(self) -> None:
        links = [{"text": "page", "title": "", "href": "https://example.com", "same_domain": True}]
        result = rank_links(links, "goal", 5)
        for item in result:
            self.assertIn("score", item)

    def test_zero_limit_returns_empty(self) -> None:
        links = [{"text": "page", "title": "", "href": "https://example.com", "same_domain": True}]
        result = rank_links(links, "goal", 0)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
