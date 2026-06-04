"""P10.1 commit 2 — ToolSpec search primitive + tool_search meta-tool foundation.

Read-only search over the existing tool registry, and a tool_search helper that
activates only eligible (classified, enabled, non-forbidden, non-side-effect)
tools for a run via the existing run-scoped deferred_tools store. No provider
dispatch; the executor still enforces policy/scope/approval.
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

from app.application.tool_registry.runtime import search_tool_specs  # noqa: E402
from app.application.code_agent import tools  # noqa: E402
from app.application.agent_kernel import deferred_tools  # noqa: E402
from app.application.agent_kernel import executor as ex  # noqa: E402


def _spec(
    name: str,
    *,
    enabled: bool = True,
    classified: bool = True,
    permission: str = "auto",
    side_effect: bool = False,
    category: str = "general",
    source: str = "builtin",
    description: str = "",
    scopes: tuple[str, ...] = (),
) -> dict:
    return {
        "name": name,
        "display_name": name,
        "display_name_ru": "",
        "description": description,
        "description_ru": "",
        "category": category,
        "source": source,
        "scopes": list(scopes),
        "permission": permission,
        "enabled": enabled,
        "policy_classified": classified,
        "side_effect": side_effect,
    }


def _patch_registry(testcase, specs):
    patcher = patch(
        "app.application.tool_registry.runtime.list_tools_with_schemas",
        return_value=specs,
    )
    patcher.start()
    testcase.addCleanup(patcher.stop)


class SearchToolSpecsTest(unittest.TestCase):
    def test_matches_by_each_field(self):
        _patch_registry(self, [
            _spec("alpha", description="fetch web pages"),
            _spec("beta", category="network"),
            _spec("gamma", source="mcp_server"),
            _spec("delta", scopes=("net.outbound",)),
            _spec("unrelated"),
        ])
        self.assertEqual([r["name"] for r in search_tool_specs("alpha")], ["alpha"])
        self.assertEqual([r["name"] for r in search_tool_specs("web pages")], ["alpha"])
        self.assertEqual([r["name"] for r in search_tool_specs("network")], ["beta"])
        self.assertEqual([r["name"] for r in search_tool_specs("mcp_server")], ["gamma"])
        self.assertEqual([r["name"] for r in search_tool_specs("net.outbound")], ["delta"])

    def test_empty_query_returns_all_sorted(self):
        _patch_registry(self, [_spec("zeta"), _spec("alpha"), _spec("mu")])
        self.assertEqual([r["name"] for r in search_tool_specs("")], ["alpha", "mu", "zeta"])

    def test_enabled_classified_safe_tool_is_activatable(self):
        _patch_registry(self, [_spec("web_search", category="web")])
        (res,) = search_tool_specs("web_search")
        self.assertTrue(res["activatable"])
        self.assertIsNone(res["reason"])
        self.assertFalse(res["side_effect"])

    def test_disabled_unclassified_forbidden_not_activatable(self):
        _patch_registry(self, [
            _spec("d", enabled=False),
            _spec("u", classified=False),
            _spec("f", permission="forbidden"),
        ])
        by_name = {r["name"]: r for r in search_tool_specs("")}
        self.assertEqual((by_name["d"]["activatable"], by_name["d"]["reason"]), (False, "disabled"))
        self.assertEqual((by_name["u"]["activatable"], by_name["u"]["reason"]), (False, "unclassified"))
        self.assertEqual((by_name["f"]["activatable"], by_name["f"]["reason"]), (False, "forbidden"))

    def test_limit_caps_results(self):
        _patch_registry(self, [_spec(f"t{i:02d}") for i in range(10)])
        out = search_tool_specs("", limit=3)
        self.assertEqual([r["name"] for r in out], ["t00", "t01", "t02"])


class ToolSearchMetaToolTest(unittest.TestCase):
    def tearDown(self):
        for rid in ("rA", "rB", "rC", "rD", "rE", "rF"):
            deferred_tools.clear_run(rid)

    def test_requires_run_id(self):
        result = tools.tool_search(run_id="  ", query="web")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "run_id_required")
        self.assertEqual(result["activated"], [])

    def test_activates_eligible_safe_tools(self):
        _patch_registry(self, [
            _spec("web_search", category="web", description="search the web"),
            _spec("read_file", category="fs"),
        ])
        deferred_tools.enable_deferred_tools("rA", ["read_file"])
        result = tools.tool_search(run_id="rA", query="")
        self.assertTrue(result["ok"])
        self.assertIn("web_search", result["activated"])
        self.assertTrue(deferred_tools.is_tool_active("rA", "web_search"))

    def test_does_not_activate_side_effect_or_blocked(self):
        _patch_registry(self, [
            _spec("run_bash", side_effect=True),
            _spec("disabled_x", enabled=False),
            _spec("unclassified_x", classified=False),
            _spec("forbidden_x", permission="forbidden"),
            _spec("safe_x"),
        ])
        deferred_tools.enable_deferred_tools("rB", [])
        result = tools.tool_search(run_id="rB", query="")
        self.assertEqual(result["activated"], ["safe_x"])
        for name in ("run_bash", "disabled_x", "unclassified_x", "forbidden_x"):
            self.assertFalse(deferred_tools.is_tool_active("rB", name))

    def test_activation_cap_enforced(self):
        _patch_registry(self, [_spec(f"t{i:02d}") for i in range(10)])
        deferred_tools.enable_deferred_tools("rC", [])
        result = tools.tool_search(run_id="rC", query="", activation_cap=3)
        self.assertEqual(len(result["activated"]), 3)
        self.assertEqual(result["activated"], ["t00", "t01", "t02"])

    def test_activation_is_run_isolated(self):
        _patch_registry(self, [_spec("web_search")])
        deferred_tools.enable_deferred_tools("rD", [])
        deferred_tools.enable_deferred_tools("rE", [])
        tools.tool_search(run_id="rD", query="")
        self.assertTrue(deferred_tools.is_tool_active("rD", "web_search"))
        self.assertFalse(deferred_tools.is_tool_active("rE", "web_search"))

    def test_noop_for_non_deferred_run(self):
        _patch_registry(self, [_spec("web_search")])
        result = tools.tool_search(run_id="rF", query="")
        # activate_tools is a no-op on a non-deferred run -> nothing activated,
        # run stays non-deferred (no behavior change for current runs).
        self.assertEqual(result["activated"], [])
        self.assertFalse(deferred_tools.is_deferred_run("rF"))
        # search still works (read-only): the tool is reported as eligible.
        self.assertTrue(any(m["name"] == "web_search" and m["activatable"] for m in result["matches"]))


class ActivatedToolStillEnforcedByExecutorTest(unittest.TestCase):
    """Activation grants visibility only — the executor still enforces policy."""

    RUN = "rG-defer"

    def setUp(self):
        deferred_tools.clear_run(self.RUN)
        self.addCleanup(deferred_tools.clear_run, self.RUN)

    def test_activated_tool_still_hits_preflight(self):
        from app.application.agent_registry.sandbox import _make_error

        deferred_tools.enable_deferred_tools(self.RUN, ["read_file"])
        deferred_tools.activate_tools(self.RUN, ["web_search"])  # now visible to the run
        self.assertTrue(deferred_tools.is_tool_active(self.RUN, "web_search"))

        spec = {
            "name": "web_search",
            "policy_classified": True,
            "enabled": True,
            "permission": "auto",
            "scopes": [],
            "max_output_chars": 50000,
        }
        req = ex.ToolExecutionRequest(
            run_id=self.RUN, agent_id="agent-1", project_scope_id="proj",
            tool_name="web_search", args={}, source="test",
        )
        dispatched = {"called": False}

        def dispatch_fn(name, args):
            dispatched["called"] = True
            return {"ok": True, "text": "x"}

        block = _make_error(agent_id="agent-1", reason="rate_limit_exceeded", message="rate limited")
        with patch("app.application.tool_registry.runtime.get_tool", return_value=spec), \
             patch("app.application.agent_registry.sandbox.preflight_or_raise", side_effect=block), \
             patch.object(ex, "_emit_blocked"):
            result = ex.execute_tool(req, dispatch_fn)

        # Activated (passed the deferred gate) yet still blocked by preflight —
        # activation did NOT bypass policy, and the provider was not dispatched.
        self.assertFalse(dispatched["called"])
        self.assertEqual(result.status, "blocked")
        self.assertNotEqual(result.error, "tool_not_activated")


class FailClosedAndClampTest(unittest.TestCase):
    """commit-2 fixup: fail-closed search eligibility + caller-cap clamping."""

    def tearDown(self):
        for rid in ("fc1", "fc2", "fc3", "fc4", "fc5"):
            deferred_tools.clear_run(rid)

    def test_invalid_permission_not_activatable(self):
        _patch_registry(self, [_spec("bad_perm", permission="bogus")])
        (res,) = search_tool_specs("bad_perm")
        self.assertFalse(res["activatable"])
        self.assertEqual(res["reason"], "invalid_permission")
        deferred_tools.enable_deferred_tools("fc1", [])
        result = tools.tool_search(run_id="fc1", query="bad_perm")
        self.assertEqual(result["activated"], [])
        self.assertFalse(deferred_tools.is_tool_active("fc1", "bad_perm"))

    def test_unknown_scope_not_activatable(self):
        _patch_registry(self, [_spec("bad_scope", scopes=("not.a.real.scope",))])
        (res,) = search_tool_specs("bad_scope")
        self.assertFalse(res["activatable"])
        self.assertEqual(res["reason"], "unknown_scope")
        deferred_tools.enable_deferred_tools("fc2", [])
        result = tools.tool_search(run_id="fc2", query="bad_scope")
        self.assertEqual(result["activated"], [])
        self.assertFalse(deferred_tools.is_tool_active("fc2", "bad_scope"))

    def test_huge_activation_cap_is_clamped(self):
        _patch_registry(self, [_spec(f"t{i:02d}") for i in range(10)])
        deferred_tools.enable_deferred_tools("fc3", [])
        result = tools.tool_search(run_id="fc3", query="", activation_cap=9999)
        self.assertEqual(len(result["activated"]), tools.TOOL_SEARCH_ACTIVATION_CAP)

    def test_huge_limit_is_clamped(self):
        _patch_registry(self, [_spec(f"t{i:02d}") for i in range(30)])
        deferred_tools.enable_deferred_tools("fc4", [])
        result = tools.tool_search(run_id="fc4", query="", limit=9999)
        self.assertLessEqual(len(result["matches"]), tools.TOOL_SEARCH_RESULT_LIMIT)

    def test_bad_string_limit_and_cap_do_not_crash(self):
        _patch_registry(self, [_spec(f"t{i:02d}") for i in range(8)])
        deferred_tools.enable_deferred_tools("fc5", [])
        result = tools.tool_search(run_id="fc5", query="", limit="abc", activation_cap="xyz")
        self.assertTrue(result["ok"])
        # bad cap -> falls back to the max, still never exceeding it
        self.assertLessEqual(len(result["activated"]), tools.TOOL_SEARCH_ACTIVATION_CAP)


if __name__ == "__main__":
    unittest.main()
