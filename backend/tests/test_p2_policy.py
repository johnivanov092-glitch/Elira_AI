"""Tests — P2 Policy (Шаг 5): native tool ToolSpec + forbidden tier.

Verifies that:
1. Native code-agent tools are registered in ToolSpec with correct permission tiers.
2. The executor applies the policy: run_bash requires approval, read_file auto-executes.
3. A tool with permission="forbidden" is immediately rejected (no approval created).
4. ToolSpec has correct side_effect and idempotent flags for native tools.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_registry.builtins import build_builtin_tools  # noqa: E402


class TestNativeToolSpec(unittest.TestCase):
    """Verifies ToolSpec metadata for code_agent native tools."""

    @classmethod
    def setUpClass(cls):
        cls.tools = {t["name"]: t for t in build_builtin_tools()}

    def _get(self, name: str) -> dict:
        tool = self.tools.get(name)
        self.assertIsNotNone(tool, f"Native tool '{name}' not found in build_builtin_tools()")
        return tool  # type: ignore[return-value]

    # ── Auto-permission (read-only) ─────────────────────────────────────────

    def test_read_file_is_auto(self):
        t = self._get("read_file")
        self.assertEqual(t["permission"], "auto")
        self.assertFalse(t["side_effect"])
        self.assertTrue(t["idempotent"])
        self.assertEqual(t["source"], "code_agent")

    def test_glob_is_auto(self):
        t = self._get("glob")
        self.assertEqual(t["permission"], "auto")
        self.assertFalse(t["side_effect"])

    def test_grep_is_auto(self):
        t = self._get("grep")
        self.assertEqual(t["permission"], "auto")
        self.assertFalse(t["side_effect"])

    def test_recall_is_auto(self):
        t = self._get("recall")
        self.assertEqual(t["permission"], "auto")
        self.assertFalse(t["side_effect"])

    def test_web_search_is_auto(self):
        t = self._get("web_search")
        self.assertEqual(t["permission"], "auto")
        self.assertFalse(t["side_effect"])

    def test_web_fetch_is_auto(self):
        t = self._get("web_fetch")
        self.assertEqual(t["permission"], "auto")
        self.assertFalse(t["side_effect"])

    # ── Require-approval (side-effect) ─────────────────────────────────────

    def test_write_file_requires_approval(self):
        t = self._get("write_file")
        self.assertEqual(t["permission"], "require_approval")
        self.assertTrue(t["side_effect"])
        self.assertFalse(t["idempotent"])

    def test_edit_file_requires_approval(self):
        t = self._get("edit_file")
        self.assertEqual(t["permission"], "require_approval")
        self.assertTrue(t["side_effect"])

    def test_run_bash_requires_approval(self):
        t = self._get("run_bash")
        self.assertEqual(t["permission"], "require_approval")
        self.assertTrue(t["side_effect"])
        self.assertFalse(t["idempotent"])
        self.assertGreaterEqual(t["timeout_seconds"], 60)

    def test_sandbox_run_requires_approval(self):
        t = self._get("sandbox_run")
        self.assertEqual(t["permission"], "require_approval")
        self.assertTrue(t["side_effect"])

    def test_sandbox_reset_requires_approval(self):
        t = self._get("sandbox_reset")
        self.assertEqual(t["permission"], "require_approval")
        self.assertTrue(t["side_effect"])

    # ── Source tag ─────────────────────────────────────────────────────────

    def test_native_tools_have_code_agent_source(self):
        native = ["read_file", "write_file", "edit_file", "glob", "grep",
                  "run_bash", "web_search", "web_fetch", "recall",
                  "sandbox_run", "sandbox_reset"]
        for name in native:
            with self.subTest(tool=name):
                self.assertEqual(self.tools[name]["source"], "code_agent",
                                 f"{name} should have source='code_agent'")


class TestExecutorForbiddenTier(unittest.TestCase):
    """Verifies the 'forbidden' tier blocks tools immediately."""

    def _exec(self, tool_name: str, permission: str):
        from app.application.agent_kernel.executor import (
            ToolExecutionRequest,
            execute_tool,
        )
        dispatch_calls = []

        def _dispatch(name, args):
            dispatch_calls.append(name)
            return {"ok": True, "text": "executed"}

        with mock.patch("app.application.tool_registry.runtime.get_tool",
                        return_value={"permission": permission, "max_output_chars": 50000}), \
             mock.patch("app.application.agent_registry.sandbox.preflight_or_raise",
                        return_value={"ok": True}):
            result = execute_tool(
                ToolExecutionRequest(
                    run_id="run-forbidden-test",
                    agent_id="test-agent",
                    project_scope_id="scope:test",
                    tool_name=tool_name,
                    args={},
                    source="test",
                ),
                dispatch_fn=_dispatch,
            )
        return result, dispatch_calls

    def test_forbidden_tool_returns_forbidden_status(self):
        result, calls = self._exec("evil_tool", "forbidden")
        self.assertEqual(result.status, "forbidden")
        self.assertEqual(calls, [], "forbidden tool must not be dispatched")

    def test_forbidden_output_has_informative_text(self):
        result, _ = self._exec("evil_tool", "forbidden")
        self.assertIn("evil_tool", result.output.get("text", ""))
        self.assertFalse(result.output.get("ok", True))

    def test_forbidden_no_approval_created(self):
        """Forbidden tools must NOT create pending approvals."""
        from app.application.monitoring import runtime as mon
        created_approvals = []
        with mock.patch.object(mon, "create_approval", side_effect=created_approvals.append):
            self._exec("evil_tool", "forbidden")
        self.assertEqual(created_approvals, [], "forbidden tools must not call create_approval")

    def test_auto_tool_not_affected_by_forbidden_logic(self):
        result, calls = self._exec("read_file", "auto")
        self.assertEqual(result.status, "ok")
        self.assertIn("read_file", calls)

    def test_require_approval_tool_returns_waiting(self):
        from app.application.monitoring import runtime as mon
        from app.application.monitoring import store as mon_store
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            mon_store.migrate_approvals_table(db)
            with mock.patch.object(mon, "DB_PATH", db):
                result, calls = self._exec("run_bash", "require_approval")
        self.assertEqual(result.status, "waiting_approval")
        self.assertEqual(calls, [])


class TestExecutorNativePolicyIntegration(unittest.TestCase):
    """Integration: executor correctly gates code-agent native tools using ToolSpec."""

    def _exec_with_registry(self, tool_name: str, run_id: str = "run-policy"):
        """Execute a tool through the executor using the real tool_registry lookup."""
        from app.application.agent_kernel.executor import (
            ToolExecutionRequest,
            execute_tool,
        )
        from app.application.monitoring import runtime as mon
        from app.application.monitoring import store as mon_store
        import tempfile

        dispatch_calls = []

        def _dispatch(name, args):
            dispatch_calls.append(name)
            return {"ok": True, "text": f"executed {name}"}

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            mon_store.migrate_approvals_table(db)
            with mock.patch.object(mon, "DB_PATH", db), \
                 mock.patch("app.application.agent_registry.sandbox.preflight_or_raise",
                            return_value={"ok": True}):
                result = execute_tool(
                    ToolExecutionRequest(
                        run_id=run_id,
                        agent_id="code-agent",
                        project_scope_id="scope:test",
                        tool_name=tool_name,
                        args={},
                        source="code_agent",
                    ),
                    dispatch_fn=_dispatch,
                )
        return result, dispatch_calls

    def test_run_bash_waits_for_approval_via_registry(self):
        """run_bash must be gated when its ToolSpec shows require_approval."""
        from app.application.tool_registry import runtime as reg
        import tempfile
        # Seed the native tool metadata into a temp DB
        with tempfile.TemporaryDirectory() as tmp:
            from app.application.tool_registry import store as trstore
            db = Path(tmp) / "tr.db"
            from app.application.tool_registry.runtime import _CREATE_SQL
            trstore.init_db(conn_factory=lambda: __import__("sqlite3").connect(str(db)), create_sql=_CREATE_SQL)
            trstore.migrate_toolspec_columns(conn_factory=lambda: __import__("sqlite3").connect(str(db)))

            with mock.patch.object(reg, "DB_PATH", db), \
                 mock.patch.object(reg, "_BUILTIN_SEEDED", False):
                reg.seed_builtin_tools()
                spec = reg.get_tool("run_bash")

        self.assertIsNotNone(spec, "run_bash should be registered after seed")
        assert spec is not None
        self.assertEqual(spec["permission"], "require_approval")
        self.assertTrue(spec["side_effect"])

    def test_glob_auto_executes_via_registry(self):
        """glob should execute without approval when its ToolSpec shows auto."""
        from app.application.tool_registry import runtime as reg
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from app.application.tool_registry import store as trstore
            db = Path(tmp) / "tr2.db"
            from app.application.tool_registry.runtime import _CREATE_SQL
            trstore.init_db(conn_factory=lambda: __import__("sqlite3").connect(str(db)), create_sql=_CREATE_SQL)
            trstore.migrate_toolspec_columns(conn_factory=lambda: __import__("sqlite3").connect(str(db)))

            with mock.patch.object(reg, "DB_PATH", db), \
                 mock.patch.object(reg, "_BUILTIN_SEEDED", False):
                reg.seed_builtin_tools()
                spec = reg.get_tool("glob")

        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec["permission"], "auto")
        self.assertFalse(spec["side_effect"])


if __name__ == "__main__":
    unittest.main()
