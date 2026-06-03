"""P9.2B — honest timeout taxonomy.

- in-process: OBSERVED deadline only — the executor emits tool.timeout when a
  handler overran its budget, but never pretends to cancel a synchronous call.
- MCP: stderr is drained continuously and bounded, so the pipe never fills
  (which would deadlock the server) and reading does not stop after the cap.

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

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.event_bus.runtime as eb  # noqa: E402
import app.application.tool_registry.runtime as reg  # noqa: E402
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
    def test_in_process_overrun_emits_observed_timeout_but_returns(self) -> None:
        tool = f"p92b_slow_{uuid.uuid4().hex[:8]}"
        reg.register_tool(name=tool, handler=lambda a: {"ok": True},
                          permission="auto", timeout_seconds=1, source="builtin")
        run_id = f"p92b-slow-{uuid.uuid4().hex[:8]}"
        try:
            res = _run(tool, run_id, lambda n, a: (time.sleep(1.1), {"ok": True, "ran": True})[1])
            # observed only: the call still returned (not cancelled)
            self.assertEqual(res.status, "ok")
            self.assertTrue(res.output.get("ran"))
            events, _ = eb.list_events(event_type="tool.timeout")
            self.assertTrue(
                any(e["payload"].get("run_id") == run_id and e["payload"].get("observed")
                    for e in events),
                "expected an observed tool.timeout event",
            )
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
