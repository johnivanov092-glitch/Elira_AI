from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.temporal_intent import detect_temporal_intent  # noqa: E402
from app.services.web_query_planner import plan_web_query  # noqa: E402


class WebQueryPlannerTest(unittest.TestCase):
    def test_multi_intent_query_splits_finance_and_geo_news(self) -> None:
        query = "курс доллара к тенге на сегодня и новости происшествия Алматы за 2 дня"
        temporal = detect_temporal_intent(query)

        plan = plan_web_query(query, temporal)

        self.assertTrue(plan["is_multi_intent"])
        self.assertEqual(len(plan["subqueries"]), 2)
        self.assertEqual(plan["subqueries"][0]["intent_kind"], "geo_news")
        self.assertEqual(plan["subqueries"][1]["intent_kind"], "finance")
        self.assertIn("тенге", " ".join(item["query"] for item in plan["subqueries"]).lower())
        self.assertIn("алматы", " ".join(item["query"] for item in plan["subqueries"]).lower())

    def test_single_topic_finance_query_does_not_over_split(self) -> None:
        query = "курс доллара и евро к тенге"
        temporal = detect_temporal_intent(query)

        plan = plan_web_query(query, temporal)

        self.assertFalse(plan["is_multi_intent"])
        self.assertEqual(len(plan["subqueries"]), 1)
        self.assertEqual(plan["subqueries"][0]["intent_kind"], "finance")

    def test_historical_query_stays_single(self) -> None:
        query = "что произошло в 1991 году"
        temporal = detect_temporal_intent(query)

        plan = plan_web_query(query, temporal)

        self.assertFalse(plan["is_multi_intent"])
        self.assertEqual(len(plan["subqueries"]), 1)
        self.assertEqual(plan["subqueries"][0]["intent_kind"], "historical")

    def test_three_current_world_subtopics_fit_in_one_pass(self) -> None:
        query = "курс доллара к тенге на сегодня и новости происшествия Алматы за 2 дня и цена бензина в Алматы сегодня"
        temporal = detect_temporal_intent(query)

        plan = plan_web_query(query, temporal)

        self.assertTrue(plan["is_multi_intent"])
        self.assertEqual(plan["total_subqueries"], 3)
        self.assertEqual(plan["pass_count"], 1)
        self.assertFalse(plan["overflow_applied"])

    def test_four_subtopics_use_two_passes(self) -> None:
        query = "курс доллара к тенге на сегодня и новости происшествия Алматы за 2 дня и цена бензина в Алматы сегодня и статус рейсов Астана сейчас"
        temporal = detect_temporal_intent(query)

        plan = plan_web_query(query, temporal)

        self.assertTrue(plan["is_multi_intent"])
        self.assertEqual(plan["total_subqueries"], 4)
        self.assertEqual(plan["pass_count"], 2)
        self.assertTrue(plan["overflow_applied"])
        self.assertEqual(len(plan["passes"][0]["subqueries"]), 3)
        self.assertEqual(len(plan["passes"][1]["subqueries"]), 1)

    def test_more_than_six_subtopics_are_trimmed_and_marked_uncovered(self) -> None:
        query = (
            "курс доллара к тенге на сегодня и новости происшествия Алматы за 2 дня и цена бензина в Алматы сегодня "
            "и статус рейсов Астана сейчас и погода в Шымкенте сегодня и цена золота сегодня и новости Атырау за 2 дня"
        )
        temporal = detect_temporal_intent(query)

        plan = plan_web_query(query, temporal)

        self.assertEqual(plan["total_subqueries"], 6)
        self.assertEqual(plan["pass_count"], 2)
        self.assertTrue(plan["overflow_applied"])
        self.assertGreaterEqual(len(plan["uncovered_subqueries"]), 1)


if __name__ == "__main__":
    unittest.main()
