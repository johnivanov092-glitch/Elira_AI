"""The explicit batch-start helper remains available but is not a boot hook.

FastAPI intentionally leaves MCP stopped. A model/user selects a server through
runtime_control. These tests cover only the manually invoked helper: it starts
enabled servers, skips disabled ones, and isolates per-server failures.
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

from app.application.tool_providers import mcp_runtime  # noqa: E402


class StartAllEnabledTest(unittest.TestCase):
    def test_starts_enabled_and_skips_disabled(self):
        servers = [
            {"id": "context7", "enabled": True},
            {"id": "off_one", "enabled": False},
            {"id": "dbhub", "enabled": True},
        ]
        started: list[str] = []

        def _fake_start(sid):
            started.append(sid)
            return {"ok": True, "server_id": sid}

        with mock.patch.object(mcp_runtime, "list_servers", return_value=servers), \
             mock.patch.object(mcp_runtime, "start_server", side_effect=_fake_start):
            result = mcp_runtime.start_all_enabled()

        self.assertEqual(set(started), {"context7", "dbhub"})
        self.assertEqual(set(result["started"].keys()), {"context7", "dbhub"})
        self.assertNotIn("off_one", result["started"])

    def test_one_failing_server_does_not_abort_the_rest(self):
        servers = [{"id": "bad", "enabled": True}, {"id": "good", "enabled": True}]

        def _fake_start(sid):
            if sid == "bad":
                return {"ok": False, "error": "boom"}
            return {"ok": True, "server_id": sid}

        with mock.patch.object(mcp_runtime, "list_servers", return_value=servers), \
             mock.patch.object(mcp_runtime, "start_server", side_effect=_fake_start):
            result = mcp_runtime.start_all_enabled()

        # both attempted; the good one still reported
        self.assertEqual(set(result["started"].keys()), {"bad", "good"})
        self.assertTrue(result["started"]["good"]["ok"])
        self.assertFalse(result["started"]["bad"]["ok"])


if __name__ == "__main__":
    unittest.main()
