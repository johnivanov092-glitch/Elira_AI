"""P9.2A0 — API-direct tool execution must route through the unified kernel.

Confirms that:
  - the public tool_registry.execute_tool and the /plugins/run route go through
    agent_kernel.executor (policy + approval + audit), not the raw handler;
  - approvals are matched by the same run_id AND args (a retry with the returned
    run_id executes; a retry with tampered args does not);
  - blank/whitespace run_id is blocked uniformly for approval-required tools;
  - _execute_raw stays the internal raw-dispatch primitive (no policy).
"""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.application.tool_registry.runtime as reg  # noqa: E402
from app.api.routes.agent_monitor_routes import router as monitor_router  # noqa: E402
from app.api.routes.skills_extra_routes import router as extra_router  # noqa: E402
from app.api.routes.tool_registry_routes import router as tool_router  # noqa: E402


def _make_app() -> FastAPI:
    application = FastAPI()
    application.include_router(tool_router)
    application.include_router(monitor_router)
    application.include_router(extra_router)
    return application


client = TestClient(_make_app())


class P92A0KernelRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[dict] = []
        self.tool = f"p92a0_appr_{uuid.uuid4().hex[:8]}"

        def _handler(args: dict) -> dict:
            self.calls.append(dict(args))
            return {"ok": True, "echoed": args.get("x")}

        reg.register_tool(
            name=self.tool,
            handler=_handler,
            permission="require_approval",
            source="builtin",
        )

    def tearDown(self) -> None:
        try:
            reg.delete_tool(self.tool)
        except Exception:
            pass

    def _execute(self, args: dict, run_id: str | None = None):
        body: dict = {"args": args}
        if run_id is not None:
            body["run_id"] = run_id
        return client.post(f"/api/agent-os/tools/{self.tool}/execute", json=body)

    # 1 — route creates waiting_approval and returns a stable run_id
    def test_execute_route_creates_waiting_approval_with_returned_run_id(self) -> None:
        resp = self._execute({"x": "a"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["run_id"], "route must return a non-empty run_id")
        self.assertFalse(data["ok"])
        self.assertIn("approval_id", data["result"])
        self.assertEqual(self.calls, [], "handler must NOT run before approval")

    # 2 — approve + retry with the SAME run_id executes the handler
    def test_approve_then_same_run_id_executes_handler(self) -> None:
        d1 = self._execute({"x": "a"}).json()
        run_id = d1["run_id"]
        approval_id = d1["result"]["approval_id"]

        approved = client.post(f"/api/agent-os/approvals/{approval_id}/approve")
        self.assertEqual(approved.status_code, 200)

        d2 = self._execute({"x": "a"}, run_id=run_id).json()
        self.assertTrue(d2["ok"], f"expected execution after approve, got {d2}")
        self.assertEqual(len(self.calls), 1, "handler must run exactly once after approval")

    # 3 — same run_id but different args must NOT reuse the approval
    def test_same_run_id_different_args_does_not_execute(self) -> None:
        d1 = self._execute({"x": "a"}).json()
        run_id = d1["run_id"]
        approval_id = d1["result"]["approval_id"]
        client.post(f"/api/agent-os/approvals/{approval_id}/approve")

        d2 = self._execute({"x": "TAMPERED"}, run_id=run_id).json()
        self.assertFalse(d2["ok"], "tampered args must not reuse a prior approval")
        self.assertIn("approval_id", d2["result"])
        self.assertEqual(self.calls, [], "handler must NOT run for unapproved args")

    # 4 — /plugins/run goes through the kernel and returns a stable run_id
    def test_plugins_run_routes_through_kernel(self) -> None:
        ptool = f"p92a0_plugin_{uuid.uuid4().hex[:8]}"
        reg.register_tool(
            name=ptool,
            handler=lambda a: {"ok": True, "ran": True},
            permission="require_approval",
            source="plugin",
        )
        try:
            resp = client.post("/api/extra/plugins/run", json={"name": ptool, "args": {"x": 1}})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["run_id"], "plugins/run must return a stable run_id")
            self.assertFalse(data.get("ok", True))
            self.assertIn(
                "approval_id", data,
                "plugins/run must pass through the kernel approval gate",
            )
        finally:
            reg.delete_tool(ptool)

    # 5 — direct public runtime.execute_tool goes through the kernel
    def test_direct_public_execute_goes_through_kernel(self) -> None:
        out = reg.execute_tool(self.tool, {"x": "a"}, run_id="p92a0-direct-1")
        self.assertFalse(out["ok"])
        self.assertTrue(str(out.get("error", "")).startswith("waiting_approval"))
        self.assertEqual(self.calls, [], "kernel must gate before dispatch")

    # 6 — blank/whitespace run_id blocked uniformly for approval tools
    def test_whitespace_run_id_blocked_for_approval(self) -> None:
        out = reg.execute_tool(self.tool, {"x": "a"}, run_id="   ")
        self.assertFalse(out["ok"])
        self.assertEqual(out.get("error"), "approval_requires_run_id")
        self.assertEqual(self.calls, [])

    # 7 — _execute_raw stays the internal raw-dispatch primitive (no policy)
    def test_execute_raw_is_internal_primitive(self) -> None:
        out = reg._execute_raw(self.tool, {"x": "raw"})
        self.assertTrue(out["ok"], "_execute_raw must invoke the handler directly, bypassing policy")
        self.assertEqual(out.get("echoed"), "raw")
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
