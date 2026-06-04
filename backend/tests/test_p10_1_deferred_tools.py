"""P10.1 commit 1 — run-scoped deferred tool activation + executor enforcement.

Foundation slice: the in-memory run-scoped activation store and the single
executor gate that blocks an unactivated tool before dispatch. No agent is wired
to deferred mode yet, so existing runs are unaffected (inert by default).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.agent_kernel import deferred_tools  # noqa: E402
from app.application.agent_kernel import executor as ex  # noqa: E402


class DeferredToolsStoreTest(unittest.TestCase):
    def setUp(self):
        for rid in ("r1", "r2", "  r3  "):
            deferred_tools.clear_run(rid)
        self.addCleanup(deferred_tools.clear_run, "r1")
        self.addCleanup(deferred_tools.clear_run, "r2")
        self.addCleanup(deferred_tools.clear_run, "r3")

    def test_unknown_run_is_not_deferred(self):
        self.assertFalse(deferred_tools.is_deferred_run("r1"))
        self.assertFalse(deferred_tools.is_tool_active("r1", "web_search"))
        self.assertEqual(deferred_tools.get_active_tools("r1"), set())

    def test_enable_seeds_base_and_marks_deferred(self):
        deferred_tools.enable_deferred_tools("r1", ["read_file", "glob", "tool_search"])
        self.assertTrue(deferred_tools.is_deferred_run("r1"))
        self.assertTrue(deferred_tools.is_tool_active("r1", "read_file"))
        self.assertFalse(deferred_tools.is_tool_active("r1", "web_search"))
        self.assertEqual(
            deferred_tools.get_active_tools("r1"), {"read_file", "glob", "tool_search"}
        )

    def test_activate_adds_to_deferred_run(self):
        deferred_tools.enable_deferred_tools("r1", ["read_file"])
        result = deferred_tools.activate_tools("r1", ["web_search", "run_bash"])
        self.assertEqual(result, {"read_file", "web_search", "run_bash"})
        self.assertTrue(deferred_tools.is_tool_active("r1", "web_search"))

    def test_activate_is_noop_for_non_deferred_run(self):
        # Deferred mode must be entered explicitly; activate alone never flips it.
        result = deferred_tools.activate_tools("r2", ["web_search"])
        self.assertEqual(result, set())
        self.assertFalse(deferred_tools.is_deferred_run("r2"))
        self.assertFalse(deferred_tools.is_tool_active("r2", "web_search"))

    def test_runs_are_isolated(self):
        deferred_tools.enable_deferred_tools("r1", ["read_file"])
        deferred_tools.enable_deferred_tools("r2", ["glob"])
        deferred_tools.activate_tools("r1", ["web_search"])
        self.assertTrue(deferred_tools.is_tool_active("r1", "web_search"))
        self.assertFalse(deferred_tools.is_tool_active("r2", "web_search"))
        self.assertFalse(deferred_tools.is_tool_active("r1", "glob"))

    def test_enable_resets_active_set(self):
        deferred_tools.enable_deferred_tools("r1", ["read_file", "web_search"])
        deferred_tools.enable_deferred_tools("r1", ["glob"])
        self.assertEqual(deferred_tools.get_active_tools("r1"), {"glob"})

    def test_clear_removes_deferred_state(self):
        deferred_tools.enable_deferred_tools("r1", ["read_file"])
        deferred_tools.clear_run("r1")
        self.assertFalse(deferred_tools.is_deferred_run("r1"))

    def test_blank_run_id_is_inert(self):
        self.assertEqual(deferred_tools.enable_deferred_tools("", ["read_file"]), set())
        self.assertFalse(deferred_tools.is_deferred_run(""))
        self.assertEqual(deferred_tools.activate_tools("   ", ["x"]), set())

    def test_run_id_is_normalised(self):
        deferred_tools.enable_deferred_tools("  r3  ", ["read_file"])
        self.assertTrue(deferred_tools.is_deferred_run("r3"))
        self.assertTrue(deferred_tools.is_tool_active("r3", "read_file"))


class DeferredExecutorEnforcementTest(unittest.TestCase):
    """The run-scoped allowlist is enforced at the single executor dispatch path."""

    RUN = "run-defer-exec"

    def setUp(self):
        deferred_tools.clear_run(self.RUN)
        self.addCleanup(deferred_tools.clear_run, self.RUN)

    def _exec(self, tool_name):
        dispatched = {"called": False, "tool": None}

        def dispatch_fn(name, args):
            dispatched["called"] = True
            dispatched["tool"] = name
            return {"ok": True, "text": "done"}

        spec = {
            "name": tool_name,
            "policy_classified": True,
            "enabled": True,
            "permission": "auto",
            "scopes": [],
            "max_output_chars": 50000,
        }
        req = ex.ToolExecutionRequest(
            run_id=self.RUN,
            agent_id="agent-1",
            project_scope_id="proj",
            tool_name=tool_name,
            args={},
            source="test",
        )
        with patch("app.application.tool_registry.runtime.get_tool", return_value=spec), \
             patch("app.application.agent_registry.sandbox.preflight_or_raise", return_value={"ok": True}), \
             patch.object(ex, "_emit_blocked"), \
             patch.object(ex, "_emit_executed"):
            result = ex.execute_tool(req, dispatch_fn)
        return result, dispatched

    def test_non_deferred_run_reaches_dispatch(self):
        # Run never opted into deferred mode -> gate inert -> tool dispatches.
        result, dispatched = self._exec("web_search")
        self.assertTrue(dispatched["called"])
        self.assertEqual(result.status, "ok")

    def test_deferred_run_blocks_unactivated_tool(self):
        deferred_tools.enable_deferred_tools(self.RUN, ["read_file"])
        result, dispatched = self._exec("web_search")  # not activated
        self.assertFalse(dispatched["called"])  # blocked BEFORE provider dispatch
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error, "tool_not_activated")

    def test_deferred_run_allows_activated_tool(self):
        deferred_tools.enable_deferred_tools(self.RUN, ["read_file"])
        deferred_tools.activate_tools(self.RUN, ["web_search"])
        result, dispatched = self._exec("web_search")
        self.assertTrue(dispatched["called"])
        self.assertEqual(result.status, "ok")

    def test_deferred_run_allows_base_tool(self):
        deferred_tools.enable_deferred_tools(self.RUN, ["read_file"])
        result, dispatched = self._exec("read_file")
        self.assertTrue(dispatched["called"])
        self.assertEqual(result.status, "ok")


if __name__ == "__main__":
    unittest.main()
