"""Tool-execution heartbeat — regression for the "Соединение прервалось" bug.

A long run_bash (live: a 120s network scan) blocked with NO stream events, and the
client's 90s SSE inactivity watchdog cut the connection before the tool returned.
_exec_with_heartbeat runs the tool in a daemon thread and emits `heartbeat` events
every _LLM_HEARTBEAT_EVERY seconds so a legitimately-long tool never goes silent.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import agent_loop  # noqa: E402


class ToolHeartbeatTest(unittest.TestCase):
    def test_slow_tool_emits_heartbeats_then_result(self):
        with patch.object(agent_loop, "_LLM_HEARTBEAT_EVERY", 0.05):
            evs = list(agent_loop._exec_with_heartbeat(
                lambda: (time.sleep(0.3), "RESULT")[1], step=7))
        heartbeats = [e for e in evs if e.get("type") == "heartbeat"]
        results = [e for e in evs if "__result__" in e]
        self.assertGreaterEqual(len(heartbeats), 1, "must keep the SSE alive")
        self.assertTrue(all(e.get("step") == 7 for e in heartbeats))
        self.assertEqual(results[-1]["__result__"], "RESULT")
        self.assertIs(evs[-1], results[-1])  # result is the final item

    def test_fast_tool_emits_no_heartbeat(self):
        evs = list(agent_loop._exec_with_heartbeat(lambda: "FAST", step=1))
        self.assertEqual(evs, [{"__result__": "FAST"}])

    def test_tool_exception_propagates(self):
        def boom():
            raise RuntimeError("tool blew up")
        with self.assertRaises(RuntimeError):
            list(agent_loop._exec_with_heartbeat(boom, step=1))


if __name__ == "__main__":
    unittest.main()
