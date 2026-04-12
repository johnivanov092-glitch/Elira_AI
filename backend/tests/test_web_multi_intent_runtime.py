from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.infrastructure.search.web_search import do_web_search as _do_web_search  # noqa: E402


class WebMultiIntentRuntimeTest(unittest.TestCase):
    def test_runtime_uses_two_passes_and_marks_weak_subqueries(self) -> None:
        web_plan = {
            "is_multi_intent": True,
            "overflow_applied": True,
            "subqueries": [
                {"query": "news a", "label": "A"},
                {"query": "finance b", "label": "B"},
                {"query": "price c", "label": "C"},
                {"query": "status d", "label": "D"},
            ],
            "passes": [
                {"name": "pass_1", "subqueries": [{"query": "news a", "label": "A"}, {"query": "finance b", "label": "B"}, {"query": "price c", "label": "C"}]},
                {"name": "pass_2", "subqueries": [{"query": "status d", "label": "D"}]},
            ],
            "uncovered_subqueries": [],
        }

        fake_debug = {
            "news a": {"found": 4, "news_hits": 2, "fetched_pages": 1, "engines": ["tavily"], "local_source_hits": 2, "deeper_search_used": False, "coverage": "strong"},
            "finance b": {"found": 3, "news_hits": 0, "fetched_pages": 1, "engines": ["tavily"], "local_source_hits": 1, "deeper_search_used": False, "coverage": "strong"},
            "price c": {"found": 0, "news_hits": 0, "fetched_pages": 0, "engines": ["duckduckgo"], "local_source_hits": 0, "deeper_search_used": False, "coverage": "weak"},
            "status d": {"found": 2, "news_hits": 0, "fetched_pages": 1, "engines": ["duckduckgo"], "local_source_hits": 0, "deeper_search_used": True, "coverage": "strong"},
        }

        def fake_builder(subquery):
            query = subquery["query"]
            debug = dict(fake_debug[query])
            debug["query"] = query
            debug["label"] = subquery.get("label", query)
            return {"context": f"section for {query}", "debug": debug}

        timeline = []
        tool_results = []

        with patch("app.infrastructure.search.web_search.build_single_web_subquery_context", side_effect=fake_builder):
            context = _do_web_search("composite current-world query", timeline, tool_results, web_plan=web_plan)

        self.assertIn("section for news a", context)
        self.assertIn("section for status d", context)
        result = tool_results[-1]["result"]
        self.assertEqual(result["pass_count"], 2)
        self.assertTrue(result["overflow_applied"])
        self.assertEqual(len(result["passes"]), 2)
        self.assertIn("price c", result["uncovered_subqueries"])
        self.assertEqual(result["passes"][0]["name"], "pass_1")
        self.assertEqual(result["passes"][1]["name"], "pass_2")


if __name__ == "__main__":
    unittest.main()
