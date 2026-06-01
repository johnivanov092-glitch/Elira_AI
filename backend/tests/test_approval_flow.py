"""Tests — Approval flow (P1 Шаг 4).

Verifies that:
1. require_approval tools return waiting_approval on first call.
2. Approving the request lets the next call proceed.
3. Rejecting a pending approval keeps the gate closed.
4. Approvals expire after TTL.
5. Approvals are one-shot (used once, then need a new approval).
6. auto permission tools bypass the gate.
7. API routes work: list, get, approve, reject.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.monitoring import store as mon_store  # noqa: E402
from app.application.monitoring import runtime as mon_runtime  # noqa: E402


def _temp_db():
    """Return a temporary DB path with approvals table initialised."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = Path(tmp.name)
    mon_store.migrate_approvals_table(db)
    return db


class TestApprovalStoreCrud(unittest.TestCase):

    def setUp(self):
        self.db = _temp_db()

    def tearDown(self):
        try:
            self.db.unlink(missing_ok=True)
        except Exception:
            pass

    def test_create_and_get_approval(self):
        a = mon_store.create_approval(
            self.db,
            id="apr-1",
            tool_name="write_file",
            agent_id="code-agent",
            source="code_agent",
            run_id="run-1",
            project_scope_id="scope:abc",
            args={"path": "out.txt", "content": "hi"},
        )
        self.assertEqual(a["id"], "apr-1")
        self.assertEqual(a["status"], "pending")
        self.assertEqual(a["tool_name"], "write_file")
        self.assertEqual(a["args"], {"path": "out.txt", "content": "hi"})

        fetched = mon_store.get_approval(self.db, "apr-1")
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["status"], "pending")

    def test_approve_and_find(self):
        mon_store.create_approval(
            self.db,
            id="apr-2",
            tool_name="git_commit_push",
            agent_id="code-agent",
            run_id="run-2",
            project_scope_id="scope:x",
        )
        mon_store.update_approval_status(self.db, "apr-2", status="approved")
        found = mon_store.find_approved_approval(
            self.db, tool_name="git_commit_push", agent_id="code-agent", run_id="run-2"
        )
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found["status"], "approved")

    def test_rejected_not_findable(self):
        mon_store.create_approval(self.db, id="apr-3", tool_name="python_execute", agent_id="chat", run_id="run-3", project_scope_id="")
        mon_store.update_approval_status(self.db, "apr-3", status="rejected")
        found = mon_store.find_approved_approval(self.db, tool_name="python_execute", agent_id="chat", run_id="run-3")
        self.assertIsNone(found)

    def test_one_shot_used_approval(self):
        mon_store.create_approval(self.db, id="apr-4", tool_name="write_file", agent_id="chat", run_id="run-4", project_scope_id="")
        mon_store.update_approval_status(self.db, "apr-4", status="approved")
        mon_store.update_approval_status(self.db, "apr-4", status="used")
        found = mon_store.find_approved_approval(self.db, tool_name="write_file", agent_id="chat", run_id="run-4")
        self.assertIsNone(found)

    def test_ttl_expiry(self):
        mon_store.create_approval(self.db, id="apr-5", tool_name="write_file", agent_id="chat", run_id="run-5", project_scope_id="", ttl_seconds=1)
        time.sleep(1.1)
        mon_store.expire_old_approvals(self.db)
        a = mon_store.get_approval(self.db, "apr-5")
        self.assertIsNotNone(a)
        assert a is not None
        self.assertEqual(a["status"], "expired")

    def test_list_approvals(self):
        for i in range(3):
            mon_store.create_approval(self.db, id=f"lst-{i}", tool_name="write_file", agent_id="chat", run_id=f"r{i}", project_scope_id="")
        items = mon_store.list_approvals(self.db, status="pending")
        self.assertEqual(len(items), 3)


class TestApprovalFlowViaExecutor(unittest.TestCase):
    """Integration: executor routes require_approval tools through the gate."""

    def setUp(self):
        self.db = _temp_db()
        # Patch DB_PATH FIRST so mock records the real original value for restoration
        self._patcher = mock.patch.object(mon_runtime, "DB_PATH", self.db)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()  # Restores the real DB_PATH
        try:
            self.db.unlink(missing_ok=True)
        except Exception:
            pass

    def _exec(self, tool_name: str, permission: str, run_id: str = "run-test"):
        from app.application.agent_kernel.executor import (
            ToolExecutionRequest,
            execute_tool,
        )
        _spec = {"permission": permission, "max_output_chars": 50000}
        dispatch_calls = []

        def _dispatch(name, args):
            dispatch_calls.append(name)
            return {"ok": True, "text": f"executed {name}"}

        with mock.patch("app.application.tool_registry.runtime.get_tool", return_value=_spec), \
             mock.patch("app.application.agent_registry.sandbox.preflight_or_raise", return_value={"ok": True}):
            result = execute_tool(
                ToolExecutionRequest(
                    run_id=run_id,
                    agent_id="test-agent",
                    project_scope_id="scope:test",
                    tool_name=tool_name,
                    args={"x": 1},
                    source="test",
                ),
                dispatch_fn=_dispatch,
            )
        return result, dispatch_calls

    def test_auto_tool_passes_without_approval(self):
        result, calls = self._exec("search_memory", "auto")
        self.assertEqual(result.status, "ok")
        self.assertIn("search_memory", calls)

    def test_require_approval_tool_waits_first_call(self):
        result, calls = self._exec("write_file", "require_approval")
        self.assertEqual(result.status, "waiting_approval")
        self.assertEqual(calls, [])
        self.assertIn("approval_id", result.output)

    def test_require_approval_proceeds_after_approval(self):
        # First call: waiting
        result1, _ = self._exec("write_file", "require_approval", run_id="run-a")
        self.assertEqual(result1.status, "waiting_approval")
        approval_id = result1.output["approval_id"]

        # Approve it
        mon_store.update_approval_status(self.db, approval_id, status="approved")

        # Second call: proceeds
        result2, calls2 = self._exec("write_file", "require_approval", run_id="run-a")
        self.assertEqual(result2.status, "ok")
        self.assertIn("write_file", calls2)

    def test_approval_is_one_shot(self):
        # First call: waiting, approve, second call proceeds, third call waits again
        result1, _ = self._exec("git_commit_push", "require_approval", run_id="run-b")
        mon_store.update_approval_status(self.db, result1.output["approval_id"], status="approved")

        result2, _ = self._exec("git_commit_push", "require_approval", run_id="run-b")
        self.assertEqual(result2.status, "ok")

        # Third call without new approval → waits again
        result3, calls3 = self._exec("git_commit_push", "require_approval", run_id="run-b")
        self.assertEqual(result3.status, "waiting_approval")
        self.assertEqual(calls3, [])

    def test_rejected_approval_keeps_gate_closed(self):
        result1, _ = self._exec("python_execute", "require_approval", run_id="run-c")
        mon_store.update_approval_status(self.db, result1.output["approval_id"], status="rejected")
        result2, calls = self._exec("python_execute", "require_approval", run_id="run-c")
        self.assertEqual(result2.status, "waiting_approval")
        self.assertEqual(calls, [])


class TestApprovalRoutes(unittest.TestCase):
    """API routes: list, get, approve, reject."""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.agent_monitor_routes import router

        self.db = _temp_db()
        self._patcher = mock.patch.object(mon_runtime, "DB_PATH", self.db)
        self._patcher.start()

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self._patcher.stop()
        try:
            self.db.unlink(missing_ok=True)
        except Exception:
            pass

    def _create(self, *, id_="rt-1", tool="write_file", agent="chat", run="run-rt"):
        mon_store.create_approval(self.db, id=id_, tool_name=tool, agent_id=agent, run_id=run, project_scope_id="")

    def test_list_approvals_empty(self):
        r = self.client.get("/api/agent-os/approvals")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total"], 0)

    def test_list_approvals_with_items(self):
        self._create(id_="rt-a")
        r = self.client.get("/api/agent-os/approvals")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total"], 1)

    def test_get_approval_not_found(self):
        r = self.client.get("/api/agent-os/approvals/nope")
        self.assertEqual(r.status_code, 404)

    def test_get_approval_found(self):
        self._create(id_="rt-b")
        r = self.client.get("/api/agent-os/approvals/rt-b")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id"], "rt-b")

    def test_approve_pending(self):
        self._create(id_="rt-c")
        r = self.client.post("/api/agent-os/approvals/rt-c/approve")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "approved")

    def test_reject_pending(self):
        self._create(id_="rt-d")
        r = self.client.post("/api/agent-os/approvals/rt-d/reject")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "rejected")

    def test_cannot_approve_non_pending(self):
        self._create(id_="rt-e")
        mon_store.update_approval_status(self.db, "rt-e", status="approved")
        r = self.client.post("/api/agent-os/approvals/rt-e/approve")
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
