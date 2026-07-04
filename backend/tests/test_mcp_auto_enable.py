"""MCP tools are auto-enabled on discovery (user added the server → trusted).

Before, every MCP tool landed fail-closed (forbidden + disabled + unclassified)
and stayed blocked until an admin classified it by hand — so MCP tools were "off
by default". Now sync_mcp_tool_specs flips still-unclassified LIVE MCP rows to
enabled + policy_classified + require_approval, unless ELIRA_MCP_AUTO_ENABLE=0.
A manual disable (classified but enabled=0) must be preserved, not clobbered.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_providers import mcp_provider  # noqa: E402


class _FakeProvider:
    def __init__(self, names):
        self._names = names

    def get_schemas(self):
        return [{"function": {"name": n, "description": "", "parameters": {}}} for n in self._names]


def _run_sync(registry_rows, live_names, env_value="1"):
    """Drive sync_mcp_tool_specs with the registry mocked; capture update_tool calls."""
    updates = []
    with mock.patch("app.application.tool_registry.runtime.register_dynamic_tool"), \
         mock.patch("app.application.tool_registry.runtime.list_tools_with_schemas",
                    return_value=registry_rows), \
         mock.patch("app.application.tool_registry.runtime.update_tool",
                    side_effect=lambda n, u: updates.append((n, u))), \
         mock.patch.dict(os.environ, {"ELIRA_MCP_AUTO_ENABLE": env_value}):
        mcp_provider.sync_mcp_tool_specs([_FakeProvider(live_names)])
    return dict(updates)


class McpAutoEnableTest(unittest.TestCase):
    def test_unclassified_live_tool_is_auto_enabled(self):
        rows = [{"name": "srv__query", "enabled": False, "policy_classified": False}]
        upd = _run_sync(rows, ["srv__query"])
        self.assertEqual(
            upd.get("srv__query"),
            {"enabled": True, "permission": "require_approval", "policy_classified": True},
        )

    def test_manual_disable_is_preserved(self):
        # classified but disabled = the admin turned it off → must stay off
        rows = [{"name": "srv__danger", "enabled": False, "policy_classified": True}]
        upd = _run_sync(rows, ["srv__danger"])
        self.assertNotIn("srv__danger", upd)

    def test_stale_tool_is_disabled(self):
        # advertised by no running server anymore → disabled, not re-enabled
        rows = [{"name": "srv__gone", "enabled": True, "policy_classified": True}]
        upd = _run_sync(rows, live_names=[])  # nothing live
        self.assertEqual(upd.get("srv__gone"), {"enabled": False})

    def test_kill_switch_keeps_fail_closed(self):
        rows = [{"name": "srv__query", "enabled": False, "policy_classified": False}]
        upd = _run_sync(rows, ["srv__query"], env_value="0")
        self.assertNotIn("srv__query", upd)  # left blocked

    def test_helper_reads_env(self):
        with mock.patch.dict(os.environ, {"ELIRA_MCP_AUTO_ENABLE": "1"}):
            self.assertTrue(mcp_provider._mcp_auto_enable())
        for off in ("0", "false", "no", "off"):
            with mock.patch.dict(os.environ, {"ELIRA_MCP_AUTO_ENABLE": off}):
                self.assertFalse(mcp_provider._mcp_auto_enable())
        # default (unset) → ON
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ELIRA_MCP_AUTO_ENABLE", None)
            self.assertTrue(mcp_provider._mcp_auto_enable())


if __name__ == "__main__":
    unittest.main()
