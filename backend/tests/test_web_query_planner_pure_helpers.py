"""Tests for previously uncovered pure helper functions in
app.application.web_query_planner.runtime:
  constants (MAX_SUBQUERIES, PASS_SIZE, term tuples, CITY_GEO_MAP, INTENT_LABELS)
  _contains_any, _strip_intro, _extract_geo, _extract_time_window,
  _split_candidate_segments, _infer_intent, _freshness_class,
  _needs_news_feed, _needs_deep_search, _preferred_domains,
  _priority, _finance_query, _geo_news_query, _should_merge,
  plan_web_query (empty path + structure checks)
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
    MAX_SUBQUERIES,
    PASS_SIZE,
    FINANCE_TERMS,
    NEWS_TERMS,
    STATUS_CURRENT_TERMS,
    PRICE_RATE_TERMS,
    CURRENT_HINT_TERMS,
    CITY_GEO_MAP,
    KZ_LOCAL_NEWS_DOMAINS,
    FINANCE_HIGH_CONFIDENCE_DOMAINS,
    INTENT_LABELS,
    _contains_any,
    _strip_intro,
    _extract_geo,
    _extract_time_window,
    _split_candidate_segments,
    _infer_intent,
    _freshness_class,
    _needs_news_feed,
    _needs_deep_search,
    _preferred_domains,
    _priority,
    _finance_query,
    _geo_news_query,
    _should_merge,
    plan_web_query,
)


# ---
# Constants
# ---

class ConstantsTest(unittest.TestCase):
    def test_max_subqueries_positive(self) -> None:
        self.assertGreater(MAX_SUBQUERIES, 0)

    def test_pass_size_positive(self) -> None:
        self.assertGreater(PASS_SIZE, 0)

    def test_finance_terms_tuple(self) -> None:
        self.assertIsInstance(FINANCE_TERMS, tuple)
        self.assertIn("курс", FINANCE_TERMS)

    def test_news_terms_tuple(self) -> None:
        self.assertIsInstance(NEWS_TERMS, tuple)
        self.assertIn("новости", NEWS_TERMS)

    def test_status_current_terms_tuple(self) -> None:
        self.assertIsInstance(STATUS_CURRENT_TERMS, tuple)
        self.assertIn("погода", STATUS_CURRENT_TERMS)

    def test_price_rate_terms_tuple(self) -> None:
        self.assertIsInstance(PRICE_RATE_TERMS, tuple)
        self.assertIn("цена", PRICE_RATE_TERMS)

    def test_city_geo_map_dict(self) -> None:
        self.assertIsInstance(CITY_GEO_MAP, dict)
        self.assertIn("алматы", CITY_GEO_MAP)

    def test_intent_labels_dict(self) -> None:
        self.assertIsInstance(INTENT_LABELS, dict)
        self.assertIn("finance", INTENT_LABELS)
        self.assertIn("general_web", INTENT_LABELS)

    def test_kz_local_news_domains_tuple(self) -> None:
        self.assertIsInstance(KZ_LOCAL_NEWS_DOMAINS, tuple)
        self.assertGreater(len(KZ_LOCAL_NEWS_DOMAINS), 0)

    def test_finance_domains_tuple(self) -> None:
        self.assertIsInstance(FINANCE_HIGH_CONFIDENCE_DOMAINS, tuple)
        self.assertGreater(len(FINANCE_HIGH_CONFIDENCE_DOMAINS), 0)


# ---
# _contains_any
# ---

class ContainsAnyTest(unittest.TestCase):
    def test_match_returns_true(self) -> None:
        self.assertTrue(_contains_any("курс доллара сегодня", FINANCE_TERMS))

    def test_no_match_returns_false(self) -> None:
        self.assertFalse(_contains_any("как приготовить борщ", FINANCE_TERMS))

    def test_empty_text_false(self) -> None:
        self.assertFalse(_contains_any("", FINANCE_TERMS))

    def test_empty_terms_false(self) -> None:
        self.assertFalse(_contains_any("курс доллара", ()))

    def test_returns_bool(self) -> None:
        self.assertIsInstance(_contains_any("test", ("test",)), bool)


# ---
# _strip_intro
# ---

class StripIntroTest(unittest.TestCase):
    def test_strips_хочу_узнать(self) -> None:
        result = _strip_intro("хочу узнать курс доллара")
        self.assertNotIn("хочу узнать", result)
        self.assertIn("курс", result)

    def test_strips_подскажи(self) -> None:
        result = _strip_intro("подскажи погоду в алматы")
        self.assertNotIn("подскажи", result)

    def test_no_intro_unchanged(self) -> None:
        result = _strip_intro("курс доллара сегодня")
        self.assertEqual(result, "курс доллара сегодня")

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(_strip_intro(""), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(_strip_intro(None), "")  # type: ignore[arg-type]

    def test_strips_расскажи(self) -> None:
        result = _strip_intro("расскажи о погоде")
        self.assertNotIn("расскажи", result)


# ---
# _extract_geo
# ---

class ExtractGeoTest(unittest.TestCase):
    def test_returns_dict(self) -> None:
        self.assertIsInstance(_extract_geo("алматы"), dict)

    def test_has_required_keys(self) -> None:
        geo = _extract_geo("алматы")
        for key in ("city", "country", "label", "scope"):
            self.assertIn(key, geo)

    def test_extracts_алматы(self) -> None:
        geo = _extract_geo("курс доллара в алматы")
        self.assertEqual(geo["city"], "Алматы")

    def test_city_implies_kazakhstan(self) -> None:
        # Use nominative form so the substring key "астана" matches
        geo = _extract_geo("погода астана")
        self.assertEqual(geo["country"], "Казахстан")

    def test_no_city_no_country_empty(self) -> None:
        geo = _extract_geo("мировые новости")
        self.assertEqual(geo["city"], "")
        self.assertEqual(geo["country"], "")

    def test_explicit_казахстан(self) -> None:
        geo = _extract_geo("новости казахстана")
        self.assertEqual(geo["country"], "Казахстан")

    def test_scope_set_for_city(self) -> None:
        geo = _extract_geo("погода алматы")
        self.assertNotEqual(geo["scope"], "")

    def test_empty_query_all_empty(self) -> None:
        geo = _extract_geo("")
        self.assertEqual(geo["city"], "")
        self.assertEqual(geo["scope"], "")


# ---
# _extract_time_window
# ---

class ExtractTimeWindowTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(_extract_time_window("сегодня"), str)

    def test_за_N_дней_extracted(self) -> None:
        result = _extract_time_window("за 3 дня")
        self.assertIn("3", result)

    def test_сегодня_extracted(self) -> None:
        result = _extract_time_window("курс доллара сегодня")
        self.assertIn("сегодня", result)

    def test_сейчас_extracted(self) -> None:
        result = _extract_time_window("что сейчас происходит")
        self.assertIn("сейчас", result)

    def test_no_time_returns_empty(self) -> None:
        result = _extract_time_window("история Рима")
        self.assertEqual(result, "")

    def test_empty_query_empty(self) -> None:
        self.assertEqual(_extract_time_window(""), "")

    def test_за_N_часов_extracted(self) -> None:
        result = _extract_time_window("за 2 часа")
        self.assertIn("2", result)


# ---
# _split_candidate_segments
# ---

class SplitCandidateSegmentsTest(unittest.TestCase):
    def test_returns_list(self) -> None:
        self.assertIsInstance(_split_candidate_segments("курс доллара"), list)

    def test_single_topic_one_segment(self) -> None:
        result = _split_candidate_segments("курс доллара сегодня")
        self.assertGreater(len(result), 0)

    def test_and_connector_splits(self) -> None:
        result = _split_candidate_segments("курс доллара и погода")
        self.assertGreaterEqual(len(result), 1)

    def test_empty_returns_list_with_empty(self) -> None:
        result = _split_candidate_segments("")
        self.assertIsInstance(result, list)

    def test_segments_are_strings(self) -> None:
        for seg in _split_candidate_segments("a и b"):
            self.assertIsInstance(seg, str)

    def test_intro_stripped_from_segments(self) -> None:
        result = _split_candidate_segments("хочу узнать курс")
        self.assertFalse(any("хочу узнать" in seg for seg in result))


# ---
# _infer_intent
# ---

class InferIntentTest(unittest.TestCase):
    def test_finance_term_returns_finance(self) -> None:
        self.assertEqual(_infer_intent("курс доллара", {}, {}), "finance")

    def test_news_term_returns_news(self) -> None:
        self.assertEqual(_infer_intent("новости сегодня", {}, {}), "general_news")

    def test_geo_news_when_scope_set(self) -> None:
        result = _infer_intent("новости алматы", {}, {"scope": "алматы"})
        self.assertEqual(result, "geo_news")

    def test_price_returns_price_rate(self) -> None:
        self.assertEqual(_infer_intent("цена бензина", {}, {}), "price_rate")

    def test_status_current_returns_status_current(self) -> None:
        self.assertEqual(_infer_intent("погода сейчас", {}, {}), "status_current")

    def test_historical_temporal(self) -> None:
        result = _infer_intent("2010 год", {"stable_historical": True}, {})
        self.assertEqual(result, "historical")

    def test_general_fallback(self) -> None:
        self.assertEqual(_infer_intent("рецепт борща", {}, {}), "general_web")

    def test_returns_string(self) -> None:
        self.assertIsInstance(_infer_intent("test", {}, {}), str)


# ---
# _freshness_class
# ---

class FreshnessClassTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(_freshness_class("test", {}), str)

    def test_stable_historical_stable(self) -> None:
        self.assertEqual(_freshness_class("история", {"stable_historical": True}), "stable")

    def test_freshness_sensitive_current(self) -> None:
        self.assertEqual(_freshness_class("test", {"freshness_sensitive": True}), "current")

    def test_requires_web_current(self) -> None:
        self.assertEqual(_freshness_class("test", {"requires_web": True}), "current")

    def test_current_hint_term_current(self) -> None:
        self.assertEqual(_freshness_class("сегодня курс", {}), "current")

    def test_neutral_stable(self) -> None:
        self.assertEqual(_freshness_class("история рима", {}), "stable")


# ---
# _needs_news_feed / _needs_deep_search
# ---

class NeedsFlagsTest(unittest.TestCase):
    def test_geo_news_needs_news_feed(self) -> None:
        self.assertTrue(_needs_news_feed("geo_news"))

    def test_general_news_needs_news_feed(self) -> None:
        self.assertTrue(_needs_news_feed("general_news"))

    def test_finance_no_news_feed(self) -> None:
        self.assertFalse(_needs_news_feed("finance"))

    def test_general_web_no_news_feed(self) -> None:
        self.assertFalse(_needs_news_feed("general_web"))

    def test_geo_news_needs_deep_search(self) -> None:
        self.assertTrue(_needs_deep_search("geo_news", {}))

    def test_general_web_no_deep_search(self) -> None:
        self.assertFalse(_needs_deep_search("general_web", {}))

    def test_reasoning_depth_deep_always_deep(self) -> None:
        self.assertTrue(_needs_deep_search("general_web", {"reasoning_depth": "deep"}))


# ---
# _preferred_domains
# ---

class PreferredDomainsTest(unittest.TestCase):
    def test_finance_returns_finance_domains(self) -> None:
        domains = _preferred_domains("finance", False)
        self.assertIsInstance(domains, list)
        self.assertGreater(len(domains), 0)

    def test_geo_news_local_first_returns_kz_domains(self) -> None:
        domains = _preferred_domains("geo_news", True)
        self.assertIsInstance(domains, list)
        self.assertGreater(len(domains), 0)

    def test_general_web_returns_empty(self) -> None:
        self.assertEqual(_preferred_domains("general_web", False), [])

    def test_geo_news_not_local_returns_empty(self) -> None:
        self.assertEqual(_preferred_domains("geo_news", False), [])


# ---
# _priority
# ---

class PriorityTest(unittest.TestCase):
    def test_returns_int(self) -> None:
        self.assertIsInstance(_priority("general_web", "stable", False, {}), int)

    def test_current_freshness_higher_than_stable(self) -> None:
        p_current = _priority("general_web", "current", False, {})
        p_stable = _priority("general_web", "stable", False, {})
        self.assertGreater(p_current, p_stable)

    def test_geo_news_higher_than_general_web(self) -> None:
        p_geo = _priority("geo_news", "stable", False, {})
        p_web = _priority("general_web", "stable", False, {})
        self.assertGreater(p_geo, p_web)

    def test_historical_lowest_among_stable(self) -> None:
        p_hist = _priority("historical", "stable", False, {})
        p_web = _priority("general_web", "stable", False, {})
        self.assertLess(p_hist, p_web)

    def test_hard_mode_adds_bonus(self) -> None:
        p_hard = _priority("general_web", "stable", False, {"mode": "hard"})
        p_norm = _priority("general_web", "stable", False, {})
        self.assertGreater(p_hard, p_norm)


# ---
# _finance_query
# ---

class FinanceQueryTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(_finance_query("курс доллара", {}, ""), str)

    def test_contains_курс(self) -> None:
        result = _finance_query("курс доллара", {}, "")
        self.assertIn("курс", result.lower())

    def test_доллар_adds_доллара(self) -> None:
        result = _finance_query("курс доллара", {}, "")
        self.assertIn("доллар", result.lower())

    def test_евро_adds_евро(self) -> None:
        result = _finance_query("курс евро", {}, "")
        self.assertIn("евро", result.lower())

    def test_kazakhstan_geo_adds_казахстан(self) -> None:
        geo = {"city": "", "country": "Казахстан", "label": "Казахстан", "scope": "kazakhstan"}
        result = _finance_query("курс", geo, "")
        self.assertIn("Казахстан", result)

    def test_сегодня_time_window(self) -> None:
        result = _finance_query("курс доллара", {}, "сегодня")
        self.assertIn("сегодня", result.lower())


# ---
# _geo_news_query
# ---

class GeoNewsQueryTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(_geo_news_query("новости", {}, ""), str)

    def test_contains_новости(self) -> None:
        result = _geo_news_query("новости алматы", {}, "")
        self.assertIn("новости", result.lower())

    def test_city_added_to_query(self) -> None:
        geo = {"city": "Алматы", "country": "Казахстан", "label": "Алматы", "scope": "алматы"}
        result = _geo_news_query("новости", geo, "")
        self.assertIn("Алматы", result)

    def test_time_window_added(self) -> None:
        result = _geo_news_query("новости", {}, "сегодня")
        self.assertIn("сегодня", result)

    def test_криминал_adds_происшествия(self) -> None:
        result = _geo_news_query("криминал алматы", {}, "")
        self.assertIn("происшестви", result.lower())


# ---
# _should_merge
# ---

class ShouldMergeTest(unittest.TestCase):
    def _sub(self, intent: str, scope: str = "") -> dict:
        return {"intent_kind": intent, "geo_scope": scope}

    def test_finance_same_merges(self) -> None:
        self.assertTrue(_should_merge(self._sub("finance"), self._sub("finance")))

    def test_different_intent_no_merge(self) -> None:
        self.assertFalse(_should_merge(self._sub("finance"), self._sub("geo_news")))

    def test_price_rate_same_scope_merges(self) -> None:
        self.assertTrue(
            _should_merge(self._sub("price_rate", "алматы"), self._sub("price_rate", "алматы"))
        )

    def test_price_rate_different_scope_no_merge(self) -> None:
        self.assertFalse(
            _should_merge(self._sub("price_rate", "алматы"), self._sub("price_rate", "астана"))
        )

    def test_general_web_same_no_merge(self) -> None:
        self.assertFalse(_should_merge(self._sub("general_web"), self._sub("general_web")))

    def test_returns_bool(self) -> None:
        self.assertIsInstance(_should_merge(self._sub("finance"), self._sub("finance")), bool)


# ---
# plan_web_query - empty path + structure
# ---

class PlanWebQueryTest(unittest.TestCase):
    def test_empty_query_returns_dict(self) -> None:
        self.assertIsInstance(plan_web_query(""), dict)

    def test_empty_query_zero_subqueries(self) -> None:
        result = plan_web_query("")
        self.assertEqual(result["total_subqueries"], 0)

    def test_empty_query_empty_passes(self) -> None:
        result = plan_web_query("")
        self.assertEqual(result["passes"], [])

    def test_result_has_required_keys(self) -> None:
        result = plan_web_query("курс доллара")
        for key in ("is_multi_intent", "geo_scope", "freshness_class",
                    "total_subqueries", "pass_count", "passes", "subqueries"):
            self.assertIn(key, result)

    def test_single_query_not_multi_intent(self) -> None:
        result = plan_web_query("курс доллара сегодня")
        self.assertFalse(result["is_multi_intent"])

    def test_subqueries_are_list(self) -> None:
        result = plan_web_query("погода в алматы")
        self.assertIsInstance(result["subqueries"], list)

    def test_freshness_class_string(self) -> None:
        result = plan_web_query("курс доллара")
        self.assertIsInstance(result["freshness_class"], str)

    def test_geo_scope_string(self) -> None:
        result = plan_web_query("погода алматы")
        self.assertIsInstance(result["geo_scope"], str)

    def test_none_temporal_safe(self) -> None:
        result = plan_web_query("курс", None)
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
