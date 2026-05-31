"""Tests for agent sandbox helpers and memory service facade."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.agent_registry.sandbox import (  # noqa: E402
    _normalize_tool_names,
    resolve_effective_agent_id,
    SandboxPolicyError,
    evaluate_preflight,
)
import app.application.smart_memory.store as sm_store   # noqa: E402
import app.application.memory.service as ms_rt  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# agent_sandbox — pure helpers
# ─────────────────────────────────────────────────────────────────────────────

class NormalizeToolNamesTest(unittest.TestCase):
    def test_deduplicates(self) -> None:
        result = _normalize_tool_names(["search", "calc", "search"])
        self.assertEqual(result, ["search", "calc"])

    def test_removes_none(self) -> None:
        result = _normalize_tool_names([None, "search"])
        self.assertNotIn(None, result)
        self.assertIn("search", result)

    def test_removes_empty_string(self) -> None:
        result = _normalize_tool_names(["", "calc"])
        self.assertNotIn("", result)
        self.assertIn("calc", result)

    def test_none_input_returns_empty(self) -> None:
        result = _normalize_tool_names(None)
        self.assertEqual(result, [])

    def test_preserves_order(self) -> None:
        result = _normalize_tool_names(["b", "a", "c"])
        self.assertEqual(result, ["b", "a", "c"])

    def test_strips_whitespace_in_names(self) -> None:
        result = _normalize_tool_names(["  search  "])
        self.assertEqual(result, ["search"])

    def test_returns_list(self) -> None:
        self.assertIsInstance(_normalize_tool_names(["search"]), list)


class ResolveEffectiveAgentIdTest(unittest.TestCase):
    def test_explicit_agent_id_wins(self) -> None:
        result = resolve_effective_agent_id(agent_id="my-custom-agent")
        self.assertEqual(result, "my-custom-agent")

    def test_registry_agent_id_used_when_no_explicit(self) -> None:
        result = resolve_effective_agent_id(
            registry_agent={"id": "builtin-researcher"}
        )
        self.assertEqual(result, "builtin-researcher")

    def test_profile_name_researcher_maps(self) -> None:
        result = resolve_effective_agent_id(profile_name="researcher")
        self.assertEqual(result, "builtin-researcher")

    def test_profile_name_universal_maps(self) -> None:
        result = resolve_effective_agent_id(profile_name="universal")
        self.assertEqual(result, "builtin-universal")

    def test_profile_name_coder_maps(self) -> None:
        result = resolve_effective_agent_id(profile_name="coder")
        self.assertEqual(result, "builtin-programmer")

    def test_profile_name_reviewer_maps(self) -> None:
        result = resolve_effective_agent_id(profile_name="reviewer")
        self.assertEqual(result, "builtin-reviewer")

    def test_profile_name_russian_prefix(self) -> None:
        # "исследоват" → builtin-researcher
        result = resolve_effective_agent_id(profile_name="исследователь")
        self.assertEqual(result, "builtin-researcher")

    def test_no_args_returns_universal(self) -> None:
        result = resolve_effective_agent_id()
        self.assertEqual(result, "builtin-universal")

    def test_empty_strings_return_universal(self) -> None:
        result = resolve_effective_agent_id(agent_id="", profile_name="")
        self.assertEqual(result, "builtin-universal")

    def test_explicit_overrides_profile(self) -> None:
        result = resolve_effective_agent_id(
            agent_id="override-id",
            profile_name="researcher",
        )
        self.assertEqual(result, "override-id")


class SandboxPolicyErrorTest(unittest.TestCase):
    def test_is_runtime_error(self) -> None:
        err = SandboxPolicyError(
            message="blocked", agent_id="a1", reason="rate", details={}
        )
        self.assertIsInstance(err, RuntimeError)

    def test_has_message(self) -> None:
        err = SandboxPolicyError(
            message="context too large", agent_id="a1", reason="ctx", details={}
        )
        self.assertEqual(err.message, "context too large")

    def test_has_agent_id(self) -> None:
        err = SandboxPolicyError(
            message="msg", agent_id="agent-x", reason="r", details={}
        )
        self.assertEqual(err.agent_id, "agent-x")

    def test_has_reason(self) -> None:
        err = SandboxPolicyError(
            message="msg", agent_id="a", reason="rate_limit_exceeded", details={}
        )
        self.assertEqual(err.reason, "rate_limit_exceeded")

    def test_has_details(self) -> None:
        err = SandboxPolicyError(
            message="msg", agent_id="a", reason="r",
            details={"recent_runs": 5, "limit": 3}
        )
        self.assertEqual(err.details["recent_runs"], 5)


class EvaluatePreflightTest(unittest.TestCase):
    _ALLOW_ALL_LIMIT = {
        "max_context_tokens": 0,
        "max_runs_per_hour": 0,
        "allowed_tools": [],
    }

    def _mock_limit(self, limit_dict: dict):
        return patch(
            "app.application.agent_registry.sandbox.ensure_agent_limit",
            return_value=limit_dict,
        )

    def test_preflight_ok_when_no_limits(self) -> None:
        with self._mock_limit(self._ALLOW_ALL_LIMIT):
            result = evaluate_preflight(agent_id="builtin-universal", num_ctx=1000)
        self.assertTrue(result["ok"])
        self.assertEqual(result["agent_id"], "builtin-universal")

    def test_preflight_has_selected_tools(self) -> None:
        with self._mock_limit(self._ALLOW_ALL_LIMIT):
            result = evaluate_preflight(
                agent_id="builtin-universal",
                selected_tools=["search", "calc"],
            )
        self.assertEqual(result["selected_tools"], ["search", "calc"])

    def test_preflight_raises_on_context_exceeded(self) -> None:
        limit = {**self._ALLOW_ALL_LIMIT, "max_context_tokens": 100}
        with self._mock_limit(limit):
            with self.assertRaises(SandboxPolicyError) as ctx:
                evaluate_preflight(agent_id="a1", num_ctx=200)
        self.assertEqual(ctx.exception.reason, "context_limit_exceeded")

    def test_preflight_allows_context_at_limit(self) -> None:
        limit = {**self._ALLOW_ALL_LIMIT, "max_context_tokens": 1000}
        with self._mock_limit(limit):
            result = evaluate_preflight(agent_id="a1", num_ctx=1000)
        self.assertTrue(result["ok"])

    def test_preflight_raises_on_tool_not_allowed(self) -> None:
        limit = {**self._ALLOW_ALL_LIMIT, "allowed_tools": ["search"]}
        with self._mock_limit(limit):
            with self.assertRaises(SandboxPolicyError) as ctx:
                evaluate_preflight(
                    agent_id="a1",
                    selected_tools=["search", "disallowed_tool"],
                )
        self.assertEqual(ctx.exception.reason, "tool_not_allowed")

    def test_preflight_raises_on_rate_limit(self) -> None:
        limit = {**self._ALLOW_ALL_LIMIT, "max_runs_per_hour": 3}
        with self._mock_limit(limit), patch(
            "app.application.agent_registry.sandbox.count_agent_runs_last_hour",
            return_value=3,
        ):
            with self.assertRaises(SandboxPolicyError) as ctx:
                evaluate_preflight(agent_id="a1")
        self.assertEqual(ctx.exception.reason, "rate_limit_exceeded")

    def test_preflight_ok_when_under_rate_limit(self) -> None:
        limit = {**self._ALLOW_ALL_LIMIT, "max_runs_per_hour": 10}
        with self._mock_limit(limit), patch(
            "app.application.agent_registry.sandbox.count_agent_runs_last_hour",
            return_value=5,
        ):
            result = evaluate_preflight(agent_id="a1")
        self.assertTrue(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# memory_service — facade over smart_memory (patched DB_PATH)
# ─────────────────────────────────────────────────────────────────────────────

class MemoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = sm_store.DB_PATH
        sm_store.DB_PATH = Path(self._tmpdir.name) / "smart_memory.db"
        sm_store.init_memory_db()

    def tearDown(self) -> None:
        sm_store.DB_PATH = self._orig_db
        self._tmpdir.cleanup()

    def test_list_profiles_ok(self) -> None:
        result = ms_rt.list_profiles()
        self.assertTrue(result["ok"])

    def test_list_memories_empty(self) -> None:
        result = ms_rt.list_memories("testprofile")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_list_memories_profile_normalized(self) -> None:
        result = ms_rt.list_memories("")
        self.assertEqual(result["profile"], "default")

    def test_add_memory_ok(self) -> None:
        result = ms_rt.add_memory("testprofile", "Python is great for data science")
        self.assertTrue(result["ok"])
        self.assertIn("id", result)

    def test_add_memory_has_profile_key(self) -> None:
        result = ms_rt.add_memory("myprofile", "Machine learning rocks")
        self.assertEqual(result["profile"], "myprofile")

    def test_add_memory_too_short_rejected(self) -> None:
        result = ms_rt.add_memory("testprofile", "hi")
        self.assertFalse(result["ok"])

    def test_list_memories_after_add(self) -> None:
        ms_rt.add_memory("test", "Some test memory item here")
        result = ms_rt.list_memories("test")
        self.assertGreater(result["count"], 0)

    def test_delete_memory_ok(self) -> None:
        added = ms_rt.add_memory("test", "To be deleted soon here")
        result = ms_rt.delete_memory("test", str(added["id"]))
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "test")

    def test_delete_memory_invalid_id(self) -> None:
        result = ms_rt.delete_memory("test", "not-a-number")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_delete_memory_not_found(self) -> None:
        result = ms_rt.delete_memory("test", "99999")
        self.assertFalse(result["ok"])

    def test_search_memory_empty_query(self) -> None:
        result = ms_rt.search_memory("test", "")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_search_memory_finds_match(self) -> None:
        ms_rt.add_memory("test", "Python programming language for data")
        result = ms_rt.search_memory("test", "Python programming")
        self.assertTrue(result["ok"])
        self.assertGreater(result["count"], 0)

    def test_search_memory_has_profile_key(self) -> None:
        result = ms_rt.search_memory("myprof", "query")
        self.assertEqual(result["profile"], "myprof")

    def test_build_memory_context_returns_string(self) -> None:
        result = ms_rt.build_memory_context("test", "Python")
        self.assertIsInstance(result, str)

    def test_build_memory_context_empty_when_no_memories(self) -> None:
        result = ms_rt.build_memory_context("empty_profile", "some query")
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()
