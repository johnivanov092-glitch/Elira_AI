"""Tests for pure helpers in webskill.core.web_runtime.

All functions under test are pure (no local provider calls, no HTTP, no FS):
  core/web_runtime.py:
    result_score, rerank_results, count_preferred_domain_hits,
    dedupe_results, format_search_results
"""
from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from webskill.core.web_runtime import (  # noqa: E402
    result_score,
    rerank_results,
    count_preferred_domain_hits,
    dedupe_results,
    format_search_results,
    search_web_runtime,
)
from webskill.core.web_engines import ENGINE_LABELS  # noqa: E402


# ---
# core/llm.py - _is_ctx_error
# ---


# ---
# core/llm.py - estimate_tokens
# ---


# ---
# core/llm.py - get_safe_ctx
# ---


# ---
# core/llm.py - _trim_history
# ---


# ---
# core/llm.py - budget_contexts
# ---


# ---
# core/llm.py - context_size_warning
# ---


# ---
# core/llm.py - clean_code_fence
# ---


# ---
# core/llm.py - safe_json_parse
# ---


# ---
# core/llm.py - split_models_by_type
# ---


# ---
# core/web_runtime.py - result_score
# ---

class ResultScoreTest(unittest.TestCase):
    def _item(self, href="https://example.com", engine="duckduckgo",
              title="", body=""):
        return {"href": href, "engine": engine, "title": title, "body": body}

    def test_returns_int(self) -> None:
        self.assertIsInstance(result_score(self._item()), int)

    def test_preferred_domain_boost(self) -> None:
        score_preferred = result_score(
            self._item(href="https://tengrinews.kz/story"),
            preferred_domains=["tengrinews.kz"],
        )
        score_other = result_score(self._item(href="https://other.com"))
        self.assertGreater(score_preferred, score_other)

    def test_searxng_engine_bonus(self) -> None:
        score_searxng = result_score(self._item(engine="searxng"))
        score_ddg = result_score(self._item(engine="duckduckgo"))
        self.assertGreater(score_searxng, score_ddg)

    def test_geo_news_wikipedia_penalty(self) -> None:
        score_wiki = result_score(
            self._item(href="https://wikipedia.org/x", engine="wikipedia"),
            intent_kind="geo_news",
        )
        score_ddg = result_score(self._item(engine="duckduckgo"), intent_kind="geo_news")
        self.assertLess(score_wiki, score_ddg)

    def test_finance_high_confidence_boost(self) -> None:
        score_finance = result_score(
            self._item(href="https://nationalbank.kz/rates"),
            intent_kind="finance",
        )
        score_other = result_score(self._item(), intent_kind="finance")
        self.assertGreater(score_finance, score_other)

    def test_historical_wikipedia_boost(self) -> None:
        score_wiki = result_score(
            self._item(engine="wikipedia"),
            intent_kind="historical",
        )
        score_ddg = result_score(self._item(engine="duckduckgo"), intent_kind="historical")
        self.assertGreater(score_wiki, score_ddg)

    def test_local_first_kz_boost(self) -> None:
        score_local = result_score(
            self._item(href="https://tengrinews.kz/story"),
            intent_kind="geo_news",
            local_first=True,
        )
        score_foreign = result_score(
            self._item(href="https://bbc.co.uk/news"),
            intent_kind="geo_news",
            local_first=True,
        )
        self.assertGreater(score_local, score_foreign)


# ---
# core/web_runtime.py - rerank_results
# ---

class RerankResultsTest(unittest.TestCase):
    def _items(self):
        return [
            {"href": "https://example.com", "engine": "duckduckgo", "title": "A", "body": ""},
            {"href": "https://tengrinews.kz/x", "engine": "duckduckgo", "title": "B", "body": ""},
            {"href": "https://nationalbank.kz/", "engine": "searxng", "title": "C", "body": ""},
        ]

    def test_returns_list(self) -> None:
        self.assertIsInstance(rerank_results(self._items()), list)

    def test_same_length(self) -> None:
        items = self._items()
        self.assertEqual(len(rerank_results(items)), len(items))

    def test_preferred_domain_rises_to_top(self) -> None:
        items = self._items()
        result = rerank_results(items, preferred_domains=["tengrinews.kz"])
        self.assertEqual(result[0]["href"], "https://tengrinews.kz/x")

    def test_empty_input_empty_output(self) -> None:
        self.assertEqual(rerank_results([]), [])

    def test_returns_dicts(self) -> None:
        for item in rerank_results(self._items()):
            self.assertIsInstance(item, dict)


# ---
# core/web_runtime.py - count_preferred_domain_hits
# ---

class CountPreferredDomainHitsTest(unittest.TestCase):
    def _items(self):
        return [
            {"href": "https://tengrinews.kz/story"},
            {"href": "https://nur.kz/news"},
            {"href": "https://bbc.co.uk/article"},
        ]

    def test_returns_int(self) -> None:
        self.assertIsInstance(count_preferred_domain_hits(self._items()), int)

    def test_no_preferred_returns_zero(self) -> None:
        self.assertEqual(count_preferred_domain_hits(self._items()), 0)

    def test_one_match(self) -> None:
        result = count_preferred_domain_hits(
            self._items(), preferred_domains=["tengrinews.kz"]
        )
        self.assertEqual(result, 1)

    def test_two_matches(self) -> None:
        result = count_preferred_domain_hits(
            self._items(), preferred_domains=["tengrinews.kz", "nur.kz"]
        )
        self.assertEqual(result, 2)

    def test_no_hits_returns_zero(self) -> None:
        result = count_preferred_domain_hits(
            self._items(), preferred_domains=["wikipedia.org"]
        )
        self.assertEqual(result, 0)

    def test_empty_results_zero(self) -> None:
        self.assertEqual(count_preferred_domain_hits([], preferred_domains=["tengrinews.kz"]), 0)


# ---
# core/web_runtime.py - dedupe_results
# ---

class DedupeResultsTest(unittest.TestCase):
    def _item(self, href, title="t", body="b", engine="ddg"):
        return {"href": href, "title": title, "body": body, "engine": engine}

    def test_returns_list(self) -> None:
        self.assertIsInstance(dedupe_results([]), list)

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(dedupe_results([]), [])

    def test_no_duplicates_unchanged_count(self) -> None:
        items = [self._item("https://a.com"), self._item("https://b.com")]
        self.assertEqual(len(dedupe_results(items)), 2)

    def test_duplicate_href_removed(self) -> None:
        items = [
            self._item("https://a.com"),
            self._item("https://a.com"),
        ]
        self.assertEqual(len(dedupe_results(items)), 1)

    def test_max_results_respected(self) -> None:
        items = [self._item(f"https://site{i}.com") for i in range(10)]
        result = dedupe_results(items, max_results=3)
        self.assertEqual(len(result), 3)

    def test_result_items_have_required_keys(self) -> None:
        items = [self._item("https://example.com", title="Title", body="Body")]
        result = dedupe_results(items)
        self.assertIn("title", result[0])
        self.assertIn("href", result[0])
        self.assertIn("body", result[0])
        self.assertIn("engine", result[0])

    def test_empty_href_dedupe_by_title_body(self) -> None:
        items = [
            {"href": "", "title": "same", "body": "same", "engine": "ddg"},
            {"href": "", "title": "same", "body": "same", "engine": "ddg"},
        ]
        self.assertEqual(len(dedupe_results(items)), 1)


# ---
# core/web_runtime.py - format_search_results
# ---

class FormatSearchResultsTest(unittest.TestCase):
    def _items(self):
        return [
            {"title": "First Result", "href": "https://a.com", "body": "Description A", "engine": "duckduckgo"},
            {"title": "Second Result", "href": "https://b.com", "body": "Description B", "engine": "wikipedia"},
        ]

    def test_returns_string(self) -> None:
        self.assertIsInstance(format_search_results(self._items()), str)

    def test_empty_input_empty_string(self) -> None:
        self.assertEqual(format_search_results([]), "")

    def test_contains_title(self) -> None:
        result = format_search_results(self._items())
        self.assertIn("First Result", result)

    def test_contains_href(self) -> None:
        result = format_search_results(self._items())
        self.assertIn("https://a.com", result)

    def test_contains_body(self) -> None:
        result = format_search_results(self._items())
        self.assertIn("Description A", result)

    def test_numbered_from_one(self) -> None:
        result = format_search_results(self._items())
        self.assertIn("[1]", result)
        self.assertIn("[2]", result)

    def test_engine_label_shown(self) -> None:
        result = format_search_results(self._items())
        self.assertIn(ENGINE_LABELS["duckduckgo"], result)

    def test_single_item_no_extra_separators(self) -> None:
        single = [{"title": "T", "href": "https://x.com", "body": "B", "engine": "ddg"}]
        result = format_search_results(single)
        self.assertIn("[1]", result)
        self.assertNotIn("[2]", result)


class SearchWebRuntimeRelevanceTest(unittest.TestCase):
    def test_relevant_result_beats_engine_name_collision(self) -> None:
        irrelevant = [{
            "title": "Ed Sheeran - Perfect",
            "href": "https://video.test/perfect",
            "body": "song lyrics and official music video",
            "engine": "searxng",
        }]
        relevant = [{
            "title": "Perfect World Pangu rune guide",
            "href": "https://game.test/pangu-rune",
            "body": "skill rune build for the game",
            "engine": "searxng",
        }]

        result = search_web_runtime(
            "Perfect World Pangu rune",
            max_results=1,
            engines=("searxng",),
            per_engine=2,
            resolve_search_engines_func=lambda engines: tuple(engines or ()),
            engine_funcs={
                "searxng": lambda *args, **kwargs: irrelevant + relevant,
            },
            logger_obj=logging.getLogger("search-relevance-test"),
        )

        self.assertEqual(result[0]["href"], "https://game.test/pangu-rune")

    def test_retired_engine_cannot_run_even_with_an_injected_resolver(self) -> None:
        from unittest.mock import Mock
        primary = Mock(return_value=[{"title": "Guide", "href": "https://example.org/guide",
                                     "body": "Guide", "engine": "searxng"}])
        retired = Mock(side_effect=AssertionError("retired adapter executed"))
        result = search_web_runtime(
            "Guide", max_results=3, engines=("searxng", "duckduckgo"),
            resolve_search_engines_func=lambda engines: tuple(engines or ()),
            engine_funcs={"searxng": primary, "duckduckgo": retired},
            logger_obj=logging.getLogger("single-search-test"),
        )
        primary.assert_called_once()
        retired.assert_not_called()
        self.assertEqual([item["engine"] for item in result], ["searxng"])


if __name__ == "__main__":
    unittest.main()
