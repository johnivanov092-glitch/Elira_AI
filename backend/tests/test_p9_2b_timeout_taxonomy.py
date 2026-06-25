"""P9.2B — honest timeout taxonomy.

- in-process: HARD per-class deadline. A synchronous in-process tool cannot be
  interrupted (a Python thread does not respond to a kill), so the executor runs
  dispatch in a daemon worker and joins with the tool's budget. On overrun it
  STOPS WAITING, emits tool.timeout, and FAILS the call (status="error",
  error="tool_timeout:…") — so the run's `finally` releases the global
  write-lock instead of freezing every future run. The abandoned worker is a
  daemon and dies with the process. A tool that finishes within budget returns
  normally and emits no tool.timeout.
- MCP: stderr is drained continuously and bounded, so the pipe never fills
  (which would deadlock the server) and reading does not stop after the cap.

The effective budget is max(class floor, spec.timeout_seconds): the stock
timeout_seconds default (30) is a model-request hint, never an execution budget,
so it cannot lower a class floor — only an explicit larger spec value raises it.
Class floors are >100s (a real remote tool may legitimately run 10-15 min), so
to exercise the watchdog in a unit test we monkeypatch the local floor down.

(subprocess kill+wait is provided by subprocess.run(timeout=) in the plugin
runner; MCP per-request deadline by McpClient._request — both pre-existing.)
"""
from __future__ import annotations

import sys
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.event_bus.runtime as eb  # noqa: E402
import app.application.tool_registry.runtime as reg  # noqa: E402
from app.application.agent_kernel import executor as kex  # noqa: E402
from app.application.agent_kernel.executor import (  # noqa: E402
    ToolExecutionRequest,
    execute_tool,
)
from app.application.tool_providers.mcp_client import McpClient  # noqa: E402


def _run(tool: str, run_id: str, dispatch):
    return execute_tool(
        ToolExecutionRequest(
            run_id=run_id, agent_id="p92b", project_scope_id="",
            tool_name=tool, args={}, source="test",
        ),
        dispatch_fn=dispatch,
    )


class TimeoutTaxonomyTest(unittest.TestCase):
    def test_in_process_overrun_hard_timeout_fails_call(self) -> None:
        # A local (no scope) tool gets the local-class floor as its budget. Drop
        # the floor to a sub-second value so the watchdog actually fires within a
        # unit test, then have dispatch overrun it.
        tool = f"p92b_slow_{uuid.uuid4().hex[:8]}"
        reg.register_tool(name=tool, handler=lambda a: {"ok": True},
                          permission="auto", timeout_seconds=0, source="builtin")
        run_id = f"p92b-slow-{uuid.uuid4().hex[:8]}"
        try:
            # floor 0.2s, spec value 0 → effective budget 0.2s; dispatch overruns it
            with mock.patch.object(kex, "_TIMEOUT_CLASS_LOCAL", 0.2):
                res = _run(tool, run_id,
                           lambda n, a: (time.sleep(2.0), {"ok": True, "ran": True})[1])
            # Hard timeout: the call is FAILED and abandoned, never returns ok.
            self.assertEqual(res.status, "error")
            self.assertFalse(res.output.get("ran"))
            self.assertTrue(str(res.error or "").startswith("tool_timeout:"))
            self.assertTrue(str(res.output.get("error", "")).startswith("tool_timeout:"))
            events, _ = eb.list_events(event_type="tool.timeout")
            self.assertTrue(
                any(e["payload"].get("run_id") == run_id for e in events),
                "expected a tool.timeout event for the hard-timed-out run",
            )
        finally:
            reg.delete_tool(tool)

    def test_budget_floor_not_lowered_by_small_spec_value(self) -> None:
        # spec.timeout_seconds=1 must NOT become the execution budget: a local
        # tool that finishes in well under the (real) 120s floor completes
        # normally, with no timeout — the stock 30/1 hint never kills it.
        tool = f"p92b_floor_{uuid.uuid4().hex[:8]}"
        reg.register_tool(name=tool, handler=lambda a: {"ok": True},
                          permission="auto", timeout_seconds=1, source="builtin")
        run_id = f"p92b-floor-{uuid.uuid4().hex[:8]}"
        try:
            res = _run(tool, run_id,
                       lambda n, a: (time.sleep(0.05), {"ok": True, "ran": True})[1])
            self.assertEqual(res.status, "ok")
            self.assertTrue(res.output.get("ran"))
            events, _ = eb.list_events(event_type="tool.timeout")
            self.assertFalse(any(e["payload"].get("run_id") == run_id for e in events))
        finally:
            reg.delete_tool(tool)

    def test_fast_tool_emits_no_timeout(self) -> None:
        tool = f"p92b_fast_{uuid.uuid4().hex[:8]}"
        reg.register_tool(name=tool, handler=lambda a: {"ok": True},
                          permission="auto", timeout_seconds=30, source="builtin")
        run_id = f"p92b-fast-{uuid.uuid4().hex[:8]}"
        try:
            res = _run(tool, run_id, lambda n, a: {"ok": True})
            self.assertEqual(res.status, "ok")
            events, _ = eb.list_events(event_type="tool.timeout")
            self.assertFalse(any(e["payload"].get("run_id") == run_id for e in events))
        finally:
            reg.delete_tool(tool)

    def test_mcp_stderr_drain_is_bounded_and_complete(self) -> None:
        client = McpClient.__new__(McpClient)  # bypass __init__; unit-test the drainer
        lines = [f"stderr noise line {i}\n" for i in range(5000)]  # far above the cap
        client._proc = types.SimpleNamespace(stderr=iter(lines))
        client._stderr_tail = []
        client._stderr_chars = 0

        client._drain_stderr()  # must not hang

        # bounded tail
        self.assertLessEqual(client._stderr_chars, 8000)
        # read to completion — did NOT stop after hitting the cap
        self.assertEqual(list(client._proc.stderr), [])


if __name__ == "__main__":
    unittest.main()
