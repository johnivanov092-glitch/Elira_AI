"""P9.2A2 — allowed_scopes grants + enforcement through the single executor.

A tool may run only if its declared scopes are within the agent's granted scopes
(agent_limits.allowed_scopes). An empty grant means unrestricted (mirrors
allowed_tools), so existing flows keep working until an agent is scope-restricted.
"""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.monitoring.runtime as mon  # noqa: E402
import app.application.tool_registry.runtime as reg  # noqa: E402
from app.application.agent_kernel.executor import (  # noqa: E402
    ToolExecutionRequest,
    execute_tool,
)


class ScopeEnforcementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = f"p92a2_{uuid.uuid4().hex[:8]}"
        reg.register_tool(
            name=self.tool,
            handler=lambda a: {"ok": True, "ran": True},
            permission="auto",
            scopes=["fs.write"],
            source="builtin",
        )

    def tearDown(self) -> None:
        try:
            reg.delete_tool(self.tool)
        except Exception:
            pass

    def _exec(self, agent_id: str):
        return execute_tool(
            ToolExecutionRequest(
                run_id="r", agent_id=agent_id, project_scope_id="",
                tool_name=self.tool, args={}, source="test",
            ),
            dispatch_fn=lambda _n, _a: {"ok": True, "ran": True},
        )

    def test_scope_mismatch_blocked_before_dispatch(self) -> None:
        agent = f"p92a2-restricted-{uuid.uuid4().hex[:6]}"
        mon.update_agent_limit(agent, {"allowed_scopes": ["fs.read"]})  # no fs.write
        res = self._exec(agent)
        self.assertEqual(res.status, "blocked")
        self.assertTrue(str(res.output.get("error", "")).startswith("scope_block"))

    def test_granted_scope_allows_dispatch(self) -> None:
        agent = f"p92a2-granted-{uuid.uuid4().hex[:6]}"
        mon.update_agent_limit(agent, {"allowed_scopes": ["fs.write"]})
        res = self._exec(agent)
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.output.get("ran"))

    def test_empty_grant_is_unrestricted(self) -> None:
        # A fresh agent has no allowed_scopes set (default []) → unrestricted.
        agent = f"p92a2-default-{uuid.uuid4().hex[:6]}"
        res = self._exec(agent)
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.output.get("ran"))

    def test_unknown_scope_rejected_at_registration(self) -> None:
        with self.assertRaises(ValueError):
            reg.register_tool(
                name=f"p92a2_bogus_{uuid.uuid4().hex[:6]}",
                handler=lambda a: {"ok": True},
                scopes=["bogus.scope"],
            )

    def test_allowed_scopes_roundtrip_in_limit(self) -> None:
        agent = f"p92a2-rt-{uuid.uuid4().hex[:6]}"
        mon.update_agent_limit(agent, {"allowed_scopes": ["fs.read", "net.outbound"]})
        limit = mon.get_agent_limit(agent)
        self.assertIsNotNone(limit)
        assert limit is not None
        self.assertEqual(sorted(limit["allowed_scopes"]), ["fs.read", "net.outbound"])


if __name__ == "__main__":
    unittest.main()
