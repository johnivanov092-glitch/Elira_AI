"""Tests for runtime-editable planner keyword bags."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.planner_v2 import (  # noqa: E402
    PlannerV2Service,
    _parse_user_keyword,
    get_defaults_as_strings,
    refresh_planner,
)


class ParseUserKeywordTest(unittest.TestCase):
    def test_plain_word_auto_weight_1(self):
        self.assertEqual(_parse_user_keyword("найди"), ("найди", 1))

    def test_phrase_auto_weight_by_words(self):
        self.assertEqual(_parse_user_keyword("напиши код"), ("напиши код", 2))
        self.assertEqual(_parse_user_keyword("полный анализ кода"), ("полный анализ кода", 3))

    def test_explicit_weight_override(self):
        self.assertEqual(_parse_user_keyword("найди:3"), ("найди", 3))
        self.assertEqual(_parse_user_keyword("напиши код:5"), ("напиши код", 5))

    def test_prefix_star_kept(self):
        self.assertEqual(_parse_user_keyword("код*"), ("код*", 1))
        self.assertEqual(_parse_user_keyword("код*:4"), ("код*", 4))

    def test_invalid_weight_falls_back_to_auto(self):
        self.assertEqual(_parse_user_keyword("найди:abc"), ("найди:abc", 1))


class RefreshPlannerTest(unittest.TestCase):
    def tearDown(self):
        # Restore defaults so other tests aren't affected
        refresh_planner({})

    def test_user_bag_override(self):
        """Replacing the 'code' bag with custom triggers actually shifts routing."""
        # Without override: 'мейнтейни функцию' has no matches → chat
        plan = PlannerV2Service().plan("мейнтейни функцию calc")
        self.assertEqual(plan["route"], "chat")

        # Override 'code' with a custom verb
        refresh_planner({"code": ["мейнтейн*"]})
        plan = PlannerV2Service().plan("мейнтейни функцию calc")
        self.assertEqual(plan["route"], "code", f"scores={plan['scores']}")

    def test_empty_user_dict_keeps_defaults(self):
        """refresh_planner({}) restores shipped defaults — 'нарисуй' → image."""
        refresh_planner({"code": ["custom_only"]})
        # Now image bag still uses defaults
        plan = PlannerV2Service().plan("нарисуй закат")
        self.assertEqual(plan["route"], "image")

    def test_empty_route_falls_back_to_defaults(self):
        """If user gives [] for a route, that route uses defaults, not nothing."""
        refresh_planner({"image": []})
        plan = PlannerV2Service().plan("нарисуй закат")
        self.assertEqual(plan["route"], "image")

    def test_summary_returns_counts(self):
        summary = refresh_planner({"image": ["draw", "рисуй"]})
        self.assertEqual(summary["image"], 2)
        # other bags still defaulted
        self.assertGreater(summary["code"], 5)


class GetDefaultsAsStringsTest(unittest.TestCase):
    def test_returns_all_bags(self):
        d = get_defaults_as_strings()
        for required in ("research", "code", "image", "multi_agent", "chat_only"):
            self.assertIn(required, d)
            self.assertIsInstance(d[required], list)
            self.assertGreater(len(d[required]), 0)

    def test_weight_annotation_round_trip(self):
        """Weights other than auto are encoded as ':N' suffix."""
        d = get_defaults_as_strings()
        code_list = d["code"]
        # 'напиши код' has 2 words → auto-weight 2, but defined with explicit 3
        # so it should be encoded as 'напиши код:3'
        self.assertIn("напиши код:3", code_list)
        # 'implement*' has 1 word stem → auto-weight 1, no suffix
        self.assertIn("implement*", code_list)
        # Find at least a few entries with explicit weight override
        explicit = [s for s in code_list if ":" in s and not s.endswith(":")]
        self.assertGreater(len(explicit), 0, "expected at least one explicit-weight entry")


if __name__ == "__main__":
    unittest.main()
