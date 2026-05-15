"""Tests for pure helpers across four modules.

  application/workflows/multi_agent.py    — _builtin_workflow_templates
  application/web_query_planner/runtime.py — _build_search_query
  application/response_cache/runtime.py   — _normalize_query, _query_hash
  application/monitoring/store.py         — all_known_tools, default_limit_payload

All functions are pure (no DB writes exercised; module-level bootstrap is
idempotent and creates local SQLite files only).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.workflows.multi_agent import (  # noqa: E402
    _builtin_workflow_templates,
)
from app.application.web_query_planner.runtime import (  # noqa: E402
    _build_search_query,
)
from app.application.response_cache.runtime import (  # noqa: E402
    _normalize_query,
    _query_hash,
)
from app.application.monitoring.store import (  # noqa: E402
    all_known_tools,
    default_limit_payload,
)


# ─────────────────────────────────────────────────────────────────────────────
# workflows/multi_agent.py — _builtin_workflow_templates
# ─────────────────────────────────────────────────────────────────────────────

class BuiltinWorkflowTemplatesTest(unittest.TestCase):

    def _templates(self) -> list:
        return _builtin_workflow_templates()

    # ── return type & count ───────────────────────────────────────────────────

    def test_returns_list(self) -> None:
        self.assertIsInstance(self._templates(), list)

    def test_returns_exactly_four_templates(self) -> None:
        self.assertEqual(len(self._templates()), 4)

    def test_each_item_is_dict(self) -> None:
        for t in self._templates():
            self.assertIsInstance(t, dict)

    # ── required keys ─────────────────────────────────────────────────────────

    def test_each_has_id(self) -> None:
        for t in self._templates():
            self.assertIn("id", t)

    def test_each_has_name(self) -> None:
        for t in self._templates():
            self.assertIn("name", t)

    def test_each_has_graph(self) -> None:
        for t in self._templates():
            self.assertIn("graph", t)

    def test_each_has_enabled(self) -> None:
        for t in self._templates():
            self.assertIn("enabled", t)

    def test_each_has_version(self) -> None:
        for t in self._templates():
            self.assertIn("version", t)

    def test_each_has_source(self) -> None:
        for t in self._templates():
            self.assertIn("source", t)

    # ── field values ──────────────────────────────────────────────────────────

    def test_ids_start_with_builtin_workflow(self) -> None:
        for t in self._templates():
            self.assertTrue(str(t["id"]).startswith("builtin.workflow.multi_agent."))

    def test_all_enabled_true(self) -> None:
        for t in self._templates():
            self.assertTrue(t["enabled"])

    def test_all_version_1(self) -> None:
        for t in self._templates():
            self.assertEqual(t["version"], 1)

    def test_all_source_builtin(self) -> None:
        for t in self._templates():
            self.assertEqual(t["source"], "builtin")

    # ── graph structure ───────────────────────────────────────────────────────

    def test_each_graph_is_dict(self) -> None:
        for t in self._templates():
            self.assertIsInstance(t["graph"], dict)

    def test_each_graph_has_steps(self) -> None:
        for t in self._templates():
            self.assertIn("steps", t["graph"])

    def test_each_graph_has_at_least_one_step(self) -> None:
        for t in self._templates():
            self.assertGreater(len(t["graph"]["steps"]), 0)

    # ── uniqueness ────────────────────────────────────────────────────────────

    def test_ids_are_unique(self) -> None:
        ids = [t["id"] for t in self._templates()]
        self.assertEqual(len(ids), len(set(ids)))

    # ── determinism ───────────────────────────────────────────────────────────

    def test_deterministic_count(self) -> None:
        self.assertEqual(len(self._templates()), len(self._templates()))


# ─────────────────────────────────────────────────────────────────────────────
# web_query_planner/runtime.py — _build_search_query
# ─────────────────────────────────────────────────────────────────────────────

class BuildSearchQueryTest(unittest.TestCase):

    _GEO_KZ = {
        "city": "Алматы",
        "country": "Казахстан",
        "label": "Алматы, Казахстан",
        "scope": "алматы",
    }
    _GEO_EMPTY = {"city": "", "country": "", "label": "", "scope": ""}

    def _call(self, segment: str, intent: str, geo=None, time_window: str = "") -> str:
        if geo is None:
            geo = self._GEO_EMPTY
        return _build_search_query(segment, intent, geo, time_window)

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_string(self) -> None:
        self.assertIsInstance(self._call("hello", "general_web"), str)

    def test_nonempty_for_all_intents(self) -> None:
        for intent in ("finance", "geo_news", "general_news",
                       "price_rate", "status_current", "historical", "general_web"):
            result = self._call("test query", intent, self._GEO_EMPTY)
            self.assertGreater(len(result), 0, f"Empty result for intent={intent}")

    # ── intent-specific behaviour ─────────────────────────────────────────────

    def test_general_web_returns_stripped_segment(self) -> None:
        result = self._call("  how python works  ", "general_web")
        self.assertEqual(result, "how python works")

    def test_historical_returns_stripped_segment(self) -> None:
        result = self._call("  history of science  ", "historical")
        self.assertEqual(result, "history of science")

    def test_geo_news_result_is_string(self) -> None:
        result = self._call("происшествия", "geo_news", self._GEO_KZ, "сегодня")
        self.assertIsInstance(result, str)

    def test_finance_intent_returns_string(self) -> None:
        result = self._call("курс доллара", "finance", self._GEO_KZ, "")
        self.assertIsInstance(result, str)

    def test_general_news_returns_string(self) -> None:
        result = self._call("latest developments", "general_news", self._GEO_EMPTY, "")
        self.assertIsInstance(result, str)

    def test_price_rate_returns_string(self) -> None:
        result = self._call("цены на нефть", "price_rate", self._GEO_EMPTY, "на сегодня")
        self.assertIsInstance(result, str)

    def test_status_current_returns_string(self) -> None:
        result = self._call("что происходит", "status_current", self._GEO_EMPTY, "сейчас")
        self.assertIsInstance(result, str)

    def test_unknown_intent_falls_through_to_segment(self) -> None:
        # No branch matches → falls through to `return segment.strip()`
        result = self._call("some query", "unknown_intent_xyz")
        self.assertEqual(result, "some query")


# ─────────────────────────────────────────────────────────────────────────────
# response_cache/runtime.py — _normalize_query, _query_hash
# ─────────────────────────────────────────────────────────────────────────────

class NormalizeQueryTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(_normalize_query("hello"), str)

    def test_lowercased(self) -> None:
        self.assertEqual(_normalize_query("HELLO WORLD"), "hello world")

    def test_stripped(self) -> None:
        self.assertEqual(_normalize_query("  hello  "), "hello")

    def test_punctuation_removed(self) -> None:
        result = _normalize_query("hello, world!")
        self.assertNotIn(",", result)
        self.assertNotIn("!", result)

    def test_whitespace_collapsed(self) -> None:
        result = _normalize_query("hello   world")
        self.assertEqual(result, "hello world")

    def test_empty_string(self) -> None:
        self.assertEqual(_normalize_query(""), "")

    def test_idempotent(self) -> None:
        text = "hello world"
        self.assertEqual(_normalize_query(text), _normalize_query(_normalize_query(text)))


class QueryHashTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(_query_hash("hello", "llama3", "analyst"), str)

    def test_returns_64_char_hex(self) -> None:
        h = _query_hash("hello", "llama3", "analyst")
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_deterministic(self) -> None:
        h1 = _query_hash("hello world", "gpt4", "dev")
        h2 = _query_hash("hello world", "gpt4", "dev")
        self.assertEqual(h1, h2)

    def test_different_queries_different_hashes(self) -> None:
        h1 = _query_hash("query one", "model", "profile")
        h2 = _query_hash("query two", "model", "profile")
        self.assertNotEqual(h1, h2)

    def test_different_models_different_hashes(self) -> None:
        h1 = _query_hash("query", "modelA", "profile")
        h2 = _query_hash("query", "modelB", "profile")
        self.assertNotEqual(h1, h2)

    def test_different_profiles_different_hashes(self) -> None:
        h1 = _query_hash("query", "model", "profileA")
        h2 = _query_hash("query", "model", "profileB")
        self.assertNotEqual(h1, h2)

    def test_empty_inputs_produces_hash(self) -> None:
        h = _query_hash("", "", "")
        self.assertEqual(len(h), 64)


# ─────────────────────────────────────────────────────────────────────────────
# monitoring/store.py — all_known_tools, default_limit_payload
# ─────────────────────────────────────────────────────────────────────────────

class AllKnownToolsTest(unittest.TestCase):

    def test_returns_list(self) -> None:
        self.assertIsInstance(all_known_tools(), list)

    def test_nonempty(self) -> None:
        self.assertGreater(len(all_known_tools()), 0)

    def test_all_items_are_strings(self) -> None:
        for tool in all_known_tools():
            self.assertIsInstance(tool, str)

    def test_no_empty_strings(self) -> None:
        for tool in all_known_tools():
            self.assertGreater(len(tool.strip()), 0)

    def test_no_duplicates(self) -> None:
        tools = all_known_tools()
        self.assertEqual(len(tools), len(set(tools)))

    def test_deterministic(self) -> None:
        self.assertEqual(all_known_tools(), all_known_tools())

    def test_contains_web_search(self) -> None:
        self.assertIn("web_search", all_known_tools())


class DefaultLimitPayloadTest(unittest.TestCase):

    def _payload(self, agent_id: str = "test-agent") -> dict:
        return default_limit_payload(agent_id)

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._payload(), dict)

    # ── required keys ─────────────────────────────────────────────────────────

    def test_has_agent_id(self) -> None:
        self.assertIn("agent_id", self._payload())

    def test_has_max_runs_per_hour(self) -> None:
        self.assertIn("max_runs_per_hour", self._payload())

    def test_has_max_execution_seconds(self) -> None:
        self.assertIn("max_execution_seconds", self._payload())

    def test_has_max_context_tokens(self) -> None:
        self.assertIn("max_context_tokens", self._payload())

    def test_has_allowed_tools(self) -> None:
        self.assertIn("allowed_tools", self._payload())

    def test_has_created_at(self) -> None:
        self.assertIn("created_at", self._payload())

    # ── field types ───────────────────────────────────────────────────────────

    def test_agent_id_preserved(self) -> None:
        p = default_limit_payload("my-custom-agent")
        self.assertEqual(p["agent_id"], "my-custom-agent")

    def test_max_runs_per_hour_is_int(self) -> None:
        self.assertIsInstance(self._payload()["max_runs_per_hour"], int)

    def test_max_execution_seconds_is_int(self) -> None:
        self.assertIsInstance(self._payload()["max_execution_seconds"], int)

    def test_allowed_tools_is_list(self) -> None:
        self.assertIsInstance(self._payload()["allowed_tools"], list)

    def test_allowed_tools_nonempty(self) -> None:
        self.assertGreater(len(self._payload()["allowed_tools"]), 0)

    def test_created_at_is_string(self) -> None:
        self.assertIsInstance(self._payload()["created_at"], str)

    def test_max_runs_per_hour_positive(self) -> None:
        self.assertGreater(self._payload()["max_runs_per_hour"], 0)

    def test_different_agent_ids_differ(self) -> None:
        p1 = default_limit_payload("agent-alpha")
        p2 = default_limit_payload("agent-beta")
        self.assertNotEqual(p1["agent_id"], p2["agent_id"])


if __name__ == "__main__":
    unittest.main()
