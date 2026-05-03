"""Test that run_tool() emits tool.executed events on the event bus."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.event_bus import runtime as bus  # noqa: E402
from app.application.tool_service import runtime as tool_service  # noqa: E402


class ToolExecutedEventTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_db = bus.DB_PATH
        bus.DB_PATH = Path(self._tmpdir.name) / "event_bus.db"
        bus._init_db()

    def tearDown(self) -> None:
        bus.DB_PATH = self._original_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_run_tool_emits_tool_executed(self) -> None:
        with patch(
            "app.application.tool_registry.runtime.execute_tool",
            return_value={"ok": True, "result": "dummy"},
        ):
            result = tool_service.run_tool("git_status", {"path": "."})

        self.assertTrue(result["ok"])

        events, total = bus.list_events(event_type="tool.executed", limit=10)
        self.assertEqual(total, 1)
        event = events[0]
        self.assertEqual(event["event_type"], "tool.executed")
        self.assertEqual(event["payload"]["tool_name"], "git_status")
        self.assertTrue(event["payload"]["ok"])
        self.assertIn("path", event["payload"]["args_keys"])

    def test_run_tool_emits_failed_tool_executed(self) -> None:
        with patch(
            "app.application.tool_registry.runtime.execute_tool",
            return_value={"ok": False, "error": "not found"},
        ):
            result = tool_service.run_tool("bad_tool", {})

        self.assertFalse(result["ok"])

        events, total = bus.list_events(event_type="tool.executed", limit=10)
        self.assertEqual(total, 1)
        self.assertFalse(events[0]["payload"]["ok"])
        self.assertEqual(events[0]["payload"]["tool_name"], "bad_tool")

    def test_run_tool_does_not_raise_when_bus_unavailable(self) -> None:
        """Even if event bus emission fails, run_tool must still return normally."""
        with patch(
            "app.application.tool_registry.runtime.execute_tool",
            return_value={"ok": True},
        ), patch(
            "app.application.event_bus.runtime.emit_event",
            side_effect=RuntimeError("bus down"),
        ):
            result = tool_service.run_tool("some_tool", None)

        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
