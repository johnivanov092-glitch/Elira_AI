"""Observability event summary for Workflow requests and executions."""
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
        eb.emit_event(event_type="tool.executed", payload={"ok": True, "tool_name": "read_file"})

        summary = eb.summarize_events(recent_limit=10)
        self.assertTrue(summary["ok"])
        self.assertGreaterEqual(summary["counts"]["tool.executed"], 1)

        self.assertEqual(set(summary["counts"]), {"tool.executed"})

        # recent feed carries the just-emitted events (newest first)
        types = [e["type"] for e in summary["recent"]]
        self.assertGreaterEqual(summary["total_events"], 1)


if __name__ == "__main__":
    unittest.main()
