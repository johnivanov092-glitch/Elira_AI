"""P2.11 — observability event summary.

summarize_events surfaces the operationally-important event-bus signals
(tool timeouts, fail-closed/policy blocks, pending approvals, executions) that
the /dashboard (agent_metrics) view does not — cumulative counts + a recent
activity feed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.event_bus import runtime as eb  # noqa: E402


class EventSummaryTest(unittest.TestCase):
    def test_summary_counts_and_recent_feed(self) -> None:
        eb.emit_event(event_type="tool.timeout", payload={"tool_name": "grep"})
        eb.emit_event(event_type="sandbox.policy.blocked", payload={"reason": "scope_block"})
        eb.emit_event(event_type="tool.executed", payload={"ok": True, "tool_name": "read_file"})

        summary = eb.summarize_events(recent_limit=10)
        self.assertTrue(summary["ok"])
        self.assertGreaterEqual(summary["counts"]["tool.timeout"], 1)
        self.assertGreaterEqual(summary["counts"]["sandbox.policy.blocked"], 1)
        self.assertGreaterEqual(summary["counts"]["tool.executed"], 1)

        # every operational type is reported (even when 0)
        for t in ("tool.invalid_spec", "tool.approval_pending"):
            self.assertIn(t, summary["counts"])

        # recent feed carries the just-emitted events (newest first)
        types = [e["type"] for e in summary["recent"]]
        self.assertIn("tool.timeout", types)
        self.assertGreaterEqual(summary["total_events"], 3)


if __name__ == "__main__":
    unittest.main()
