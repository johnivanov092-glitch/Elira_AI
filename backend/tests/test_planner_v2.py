"""Tests for application/planner_v2/runtime.py — PlannerV2Service."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.planner_v2.runtime import PlannerV2Service  # noqa: E402

_EMPTY_TEMPORAL = {
    "mode": "none",
    "requires_web": False,
    "stable_historical": False,
    "freshness_sensitive": False,
    "years": [],
}
_EMPTY_WEB_PLAN = {"is_multi_intent": False, "subqueries": []}


def _plan(query: str) -> dict:
    with patch(
        "app.application.planner_v2.runtime.detect_temporal_intent",
        return_value=_EMPTY_TEMPORAL,
    ), patch(
        "app.application.planner_v2.runtime.plan_web_query",
        return_value=_EMPTY_WEB_PLAN,
    ):
        return PlannerV2Service().plan(query)


class PlannerRoutingTest(unittest.TestCase):
    # ── empty input ──────────────────────────────────────────────────────────

    def test_empty_query_returns_chat_route(self) -> None:
        result = _plan("")
        self.assertEqual(result["route"], "chat")
        self.assertEqual(result["tools"], [])

    def test_whitespace_only_returns_chat_route(self) -> None:
        self.assertEqual(_plan("   ")["route"], "chat")

    # ── research route ───────────────────────────────────────────────────────

    def test_search_keyword_routes_research(self) -> None:
        result = _plan("search for Python tutorials")
        self.assertEqual(result["route"], "research")
        self.assertIn("web_search", result["tools"])

    def test_найди_keyword_routes_research(self) -> None:
        result = _plan("найди документацию по FastAPI")
        self.assertEqual(result["route"], "research")
        self.assertIn("web_search", result["tools"])

    def test_research_includes_memory_search(self) -> None:
        result = _plan("search Python docs")
        self.assertIn("memory_search", result["tools"])

    # ── code route ───────────────────────────────────────────────────────────

    def test_fix_keyword_routes_code(self) -> None:
        result = _plan("fix the bug in auth module")
        self.assertEqual(result["route"], "code")
        self.assertIn("project_patch", result["tools"])

    def test_refactor_keyword_routes_code(self) -> None:
        result = _plan("refactor the database layer")
        self.assertEqual(result["route"], "code")

    def test_implement_keyword_routes_code(self) -> None:
        result = _plan("implement a new endpoint")
        self.assertEqual(result["route"], "code")

    def test_code_route_includes_project_mode(self) -> None:
        result = _plan("fix the bug in the repo")
        self.assertIn("project_mode", result["tools"])

    def test_code_wins_over_project_when_both_match(self) -> None:
        # Both "fix" (code) and "file" (project) present — code wins
        result = _plan("fix the file structure")
        self.assertEqual(result["route"], "code")

    # ── project route ────────────────────────────────────────────────────────

    def test_project_keyword_routes_project(self) -> None:
        result = _plan("show me the project tree")
        self.assertEqual(result["route"], "project")
        self.assertIn("project_mode", result["tools"])

    def test_project_includes_library_context(self) -> None:
        result = _plan("open the backend directory")
        self.assertIn("library_context", result["tools"])

    # ── python route ─────────────────────────────────────────────────────────

    def test_python_keyword_promotes_to_code(self) -> None:
        result = _plan("run this python script")
        self.assertEqual(result["route"], "code")
        self.assertIn("python_executor", result["tools"])

    def test_python_keyword_in_chat_adds_executor(self) -> None:
        result = _plan("calculate with python")
        self.assertIn("python_executor", result["tools"])

    # ── memory keyword ───────────────────────────────────────────────────────

    def test_memory_keyword_adds_memory_search(self) -> None:
        result = _plan("remember that I use PostgreSQL")
        self.assertIn("memory_search", result["tools"])

    # ── library keyword ──────────────────────────────────────────────────────

    def test_library_keyword_adds_library_context(self) -> None:
        result = _plan("extract text from the uploaded file")
        self.assertIn("library_context", result["tools"])

    # ── web upgrade for ambiguous chat queries ────────────────────────────────

    def test_question_starter_upgrades_to_research(self) -> None:
        # "what is" matches _NEEDS_WEB_PATTERNS so chat → research
        result = _plan("what is the capital of France")
        self.assertEqual(result["route"], "research")
        self.assertIn("web_search", result["tools"])

    def test_pure_greeting_stays_chat(self) -> None:
        result = _plan("привет")
        self.assertEqual(result["route"], "chat")
        self.assertNotIn("web_search", result["tools"])

    # ── temporal_requires_web forces web_search ───────────────────────────────

    def test_temporal_requires_web_forces_research(self) -> None:
        with patch(
            "app.application.planner_v2.runtime.detect_temporal_intent",
            return_value={**_EMPTY_TEMPORAL, "requires_web": True},
        ), patch(
            "app.application.planner_v2.runtime.plan_web_query",
            return_value=_EMPTY_WEB_PLAN,
        ):
            result = PlannerV2Service().plan("tell me something")
        self.assertEqual(result["route"], "research")
        self.assertIn("web_search", result["tools"])

    # ── plan_web_query called iff web_search tool present ─────────────────────

    def test_plan_web_query_called_for_research_route(self) -> None:
        with patch(
            "app.application.planner_v2.runtime.detect_temporal_intent",
            return_value=_EMPTY_TEMPORAL,
        ) as _, patch(
            "app.application.planner_v2.runtime.plan_web_query",
            return_value={"is_multi_intent": True, "subqueries": ["q"]},
        ) as mock_pwq:
            result = PlannerV2Service().plan("search for AI news")

        mock_pwq.assert_called_once()
        self.assertTrue(result["web_plan"]["is_multi_intent"])

    def test_plan_web_query_not_called_for_chat_route(self) -> None:
        with patch(
            "app.application.planner_v2.runtime.detect_temporal_intent",
            return_value=_EMPTY_TEMPORAL,
        ), patch(
            "app.application.planner_v2.runtime.plan_web_query",
        ) as mock_pwq:
            PlannerV2Service().plan("hello there")

        mock_pwq.assert_not_called()

    # ── tools list is deduplicated ────────────────────────────────────────────

    def test_tools_list_has_no_duplicates(self) -> None:
        result = _plan("search and fix the bug in the project memory")
        self.assertEqual(len(result["tools"]), len(set(result["tools"])))

    # ── result shape ─────────────────────────────────────────────────────────

    def test_result_always_has_required_keys(self) -> None:
        for query in ["", "hello", "search docs", "fix bug", "open project"]:
            result = _plan(query)
            for key in ("route", "tools", "temporal", "web_plan"):
                self.assertIn(key, result, f"key '{key}' missing for query={query!r}")


if __name__ == "__main__":
    unittest.main()
