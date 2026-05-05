"""Tests for application/temporal_intent (pure) and
application/project_brain (mocked deps) and application/project_patch_service."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.temporal_intent.runtime import (  # noqa: E402
    _collect_years,
    _contains_any,
    detect_temporal_intent,
)
import app.application.project_brain.runtime as pb_rt  # noqa: E402
from app.application.project_brain.runtime import (  # noqa: E402
    ProjectBrainService,
    apply_patch_and_push,
)


# ─────────────────────────────────────────────────────────────────────────────
# temporal_intent — pure helper functions
# ─────────────────────────────────────────────────────────────────────────────

class ContainsAnyTest(unittest.TestCase):
    def test_match_found(self) -> None:
        self.assertTrue(_contains_any("today is sunny", ("today", "tomorrow")))

    def test_no_match(self) -> None:
        self.assertFalse(_contains_any("yesterday was great", ("today", "now")))

    def test_empty_terms(self) -> None:
        self.assertFalse(_contains_any("text", ()))

    def test_empty_text(self) -> None:
        self.assertFalse(_contains_any("", ("today",)))


class CollectYearsTest(unittest.TestCase):
    def test_finds_single_year(self) -> None:
        years = _collect_years("data from 2023")
        self.assertIn(2023, years)

    def test_finds_multiple_years(self) -> None:
        years = _collect_years("between 2018 and 2023")
        self.assertIn(2018, years)
        self.assertIn(2023, years)

    def test_deduplicates_years(self) -> None:
        years = _collect_years("2023 report for year 2023")
        self.assertEqual(years.count(2023), 1)

    def test_no_year_returns_empty(self) -> None:
        self.assertEqual(_collect_years("no year here"), [])

    def test_ignores_non_year_numbers(self) -> None:
        years = _collect_years("item 42 cost 1500")
        self.assertEqual(years, [])

    def test_sorted_ascending(self) -> None:
        years = _collect_years("2023 and 2019 and 2021")
        self.assertEqual(years, sorted(years))


class DetectTemporalIntentTest(unittest.TestCase):
    def _now(self, year=2025):
        return datetime(year, 6, 15)

    # ── mode: hard ────────────────────────────────────────────────────────────

    def test_today_is_hard(self) -> None:
        result = detect_temporal_intent("what is the weather today", now=self._now())
        self.assertEqual(result["mode"], "hard")
        self.assertTrue(result["requires_web"])
        self.assertTrue(result["freshness_sensitive"])

    def test_current_news_is_hard(self) -> None:
        result = detect_temporal_intent("latest news", now=self._now())
        self.assertEqual(result["mode"], "hard")

    def test_current_year_mention_is_hard(self) -> None:
        result = detect_temporal_intent("what happened in 2025", now=self._now(2025))
        self.assertEqual(result["mode"], "hard")

    def test_price_query_is_hard(self) -> None:
        result = detect_temporal_intent("bitcoin price", now=self._now())
        self.assertEqual(result["mode"], "hard")

    def test_russian_today_is_hard(self) -> None:
        result = detect_temporal_intent("что сегодня происходит", now=self._now())
        self.assertEqual(result["mode"], "hard")

    # ── mode: soft ────────────────────────────────────────────────────────────

    def test_past_year_only_is_soft(self) -> None:
        result = detect_temporal_intent("events of 2018", now=self._now(2025))
        self.assertIn(result["mode"], ("soft", "stable_historical", "none"))

    # ── mode: stable_historical ───────────────────────────────────────────────

    def test_historical_with_year_is_stable(self) -> None:
        result = detect_temporal_intent(
            "history of world war 2 in 1945", now=self._now(2025)
        )
        self.assertIn(result["mode"], ("stable_historical", "soft", "none"))

    # ── mode: none ────────────────────────────────────────────────────────────

    def test_general_question_is_none(self) -> None:
        result = detect_temporal_intent("how does photosynthesis work", now=self._now())
        self.assertEqual(result["mode"], "none")
        self.assertFalse(result["requires_web"])

    def test_empty_query_is_none(self) -> None:
        result = detect_temporal_intent("", now=self._now())
        self.assertEqual(result["mode"], "none")

    # ── output structure ──────────────────────────────────────────────────────

    def test_result_has_required_keys(self) -> None:
        result = detect_temporal_intent("test query", now=self._now())
        for key in ("mode", "requires_web", "freshness_sensitive", "analytic",
                    "years", "signals", "reasoning_depth", "current_year",
                    "stable_historical"):
            self.assertIn(key, result)

    def test_years_list_populated(self) -> None:
        result = detect_temporal_intent("from 2020 to 2023", now=self._now())
        self.assertIn(2020, result["years"])
        self.assertIn(2023, result["years"])

    def test_reasoning_depth_none_for_stable_query(self) -> None:
        result = detect_temporal_intent("explain thermodynamics", now=self._now())
        self.assertEqual(result["reasoning_depth"], "none")

    def test_reasoning_depth_deep_for_hard_analytic(self) -> None:
        result = detect_temporal_intent(
            "analyze current market trends and statistics", now=self._now()
        )
        self.assertIn(result["reasoning_depth"], ("deep", "standard"))

    def test_current_year_reflects_now(self) -> None:
        result = detect_temporal_intent("hello", now=datetime(2030, 1, 1))
        self.assertEqual(result["current_year"], 2030)

    def test_signals_list_populated_for_hard(self) -> None:
        result = detect_temporal_intent("current news today", now=self._now())
        self.assertIsInstance(result["signals"], list)
        self.assertGreater(len(result["signals"]), 0)

    def test_requires_web_false_for_none_mode(self) -> None:
        result = detect_temporal_intent("what is a variable", now=self._now())
        self.assertFalse(result["requires_web"])

    def test_freshness_sensitive_only_for_hard(self) -> None:
        result_hard = detect_temporal_intent("today's weather", now=self._now())
        result_none = detect_temporal_intent("what is gravity", now=self._now())
        self.assertTrue(result_hard["freshness_sensitive"])
        self.assertFalse(result_none["freshness_sensitive"])


# ─────────────────────────────────────────────────────────────────────────────
# project_brain — module-level functions (mocked inner imports)
# ─────────────────────────────────────────────────────────────────────────────

class ProjectBrainScanProjectTest(unittest.TestCase):
    def test_scan_project_ok(self) -> None:
        fake_tree = [{"name": "src", "type": "dir"}, {"name": "README.md", "type": "file"}]
        with patch(
            "app.application.project_service.runtime.list_project_tree",
            return_value=fake_tree,
        ):
            result = pb_rt.scan_project()
        self.assertTrue(result["ok"])
        self.assertEqual(result["type"], "project_scan")
        self.assertEqual(result["tree"], fake_tree)


class ProjectBrainFindCodeTest(unittest.TestCase):
    def test_find_code_ok(self) -> None:
        fake_results = [{"path": "src/main.py", "line": 1, "content": "import os"}]
        with patch(
            "app.application.project_service.runtime.search_project",
            return_value=fake_results,
        ):
            result = pb_rt.find_code("import os")
        self.assertTrue(result["ok"])
        self.assertEqual(result["type"], "search")
        self.assertEqual(result["query"], "import os")
        self.assertEqual(result["results"], fake_results)


class ProjectBrainReadFileTest(unittest.TestCase):
    def test_read_file_delegated(self) -> None:
        fake = {"ok": True, "path": "src/main.py", "content": "x = 1\n"}
        with patch(
            "app.application.project_service.runtime.read_project_file",
            return_value=fake,
        ):
            result = pb_rt.read_file("src/main.py")
        self.assertTrue(result["ok"])
        self.assertIn("content", result)


class ProjectBrainPreviewPatchTest(unittest.TestCase):
    def test_preview_patch_delegated(self) -> None:
        fake = {"ok": True, "changed": True, "diff": "-x = 1\n+x = 2\n"}
        with patch(
            "app.application.project_patch_service.runtime.ProjectPatchService.preview_patch",
            return_value=fake,
        ):
            result = pb_rt.preview_patch("src/main.py", "x = 2\n")
        self.assertTrue(result["ok"])
        self.assertIn("diff", result)


class ProjectBrainApplyPatchTest(unittest.TestCase):
    def test_apply_patch_delegated(self) -> None:
        fake = {"ok": True, "changed": True}
        with patch(
            "app.application.project_patch_service.runtime.ProjectPatchService.apply_patch",
            return_value=fake,
        ):
            result = pb_rt.apply_patch("src/main.py", "x = 2\n")
        self.assertTrue(result["ok"])

    def test_apply_patch_and_push_no_autopush(self) -> None:
        fake_preview = {"ok": True, "changed": True, "diff": "..."}
        fake_apply = {"ok": True, "changed": True}
        with patch(
            "app.application.project_patch_service.runtime.ProjectPatchService.preview_patch",
            return_value=fake_preview,
        ), patch(
            "app.application.project_patch_service.runtime.ProjectPatchService.apply_patch",
            return_value=fake_apply,
        ):
            result = apply_patch_and_push("src/main.py", "x = 2\n", auto_push=False)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["git"])

    def test_apply_patch_and_push_preview_fails_returns_error(self) -> None:
        fake_preview = {"ok": False, "error": "file not found"}
        with patch(
            "app.application.project_patch_service.runtime.ProjectPatchService.preview_patch",
            return_value=fake_preview,
        ):
            result = apply_patch_and_push("missing.py", "content")
        self.assertFalse(result["ok"])

    def test_apply_patch_and_push_apply_fails(self) -> None:
        fake_preview = {"ok": True, "changed": True, "diff": "..."}
        fake_apply = {"ok": False, "error": "write error"}
        with patch(
            "app.application.project_patch_service.runtime.ProjectPatchService.preview_patch",
            return_value=fake_preview,
        ), patch(
            "app.application.project_patch_service.runtime.ProjectPatchService.apply_patch",
            return_value=fake_apply,
        ):
            result = apply_patch_and_push("src/main.py", "x = 2\n")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["git"])


# ─────────────────────────────────────────────────────────────────────────────
# ProjectBrainService class — same ops via class wrapper
# ─────────────────────────────────────────────────────────────────────────────

class ProjectBrainServiceClassTest(unittest.TestCase):
    def setUp(self) -> None:
        self._svc = ProjectBrainService()

    def test_scan_project(self) -> None:
        with patch(
            "app.application.project_service.runtime.list_project_tree",
            return_value=[],
        ):
            result = self._svc.scan_project()
        self.assertTrue(result["ok"])

    def test_find_code(self) -> None:
        with patch(
            "app.application.project_service.runtime.search_project",
            return_value=[],
        ):
            result = self._svc.find_code("query")
        self.assertTrue(result["ok"])
        self.assertEqual(result["query"], "query")

    def test_read_file(self) -> None:
        fake = {"ok": False, "error": "not found"}
        with patch(
            "app.application.project_service.runtime.read_project_file",
            return_value=fake,
        ):
            result = self._svc.read_file("missing.py")
        self.assertFalse(result["ok"])

    def test_apply_patch_and_push_no_autopush(self) -> None:
        fake_preview = {"ok": True, "changed": True, "diff": "..."}
        fake_apply = {"ok": True, "changed": True}
        with patch(
            "app.application.project_patch_service.runtime.ProjectPatchService.preview_patch",
            return_value=fake_preview,
        ), patch(
            "app.application.project_patch_service.runtime.ProjectPatchService.apply_patch",
            return_value=fake_apply,
        ):
            result = self._svc.apply_patch_and_push("src/x.py", "new", auto_push=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["auto_push"])


if __name__ == "__main__":
    unittest.main()
