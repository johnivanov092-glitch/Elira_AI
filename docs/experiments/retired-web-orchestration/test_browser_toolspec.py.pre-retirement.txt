"""Regression: the code-agent `browser` tool must have a ToolSpec.

`browser` is advertised to the model (tool_schemas) and dispatched
(tools/_dispatch.py), so inventory metadata must remain available for
presentation and audit. Destination authorization belongs to the selected
Workflow permission mode; the browser validates only the HTTP(S) protocol.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_registry.builtins import _build_native_code_agent_tools  # noqa: E402


class BrowserToolSpecTest(unittest.TestCase):
    def setUp(self) -> None:
        self.specs = {s["name"]: s for s in _build_native_code_agent_tools()}

    def test_browser_spec_exists(self) -> None:
        self.assertIn("browser", self.specs)

    def test_browser_profile_matches_web_fetch(self) -> None:
        browser = self.specs["browser"]
        web_fetch = self.specs["web_fetch"]
        self.assertEqual(browser["permission"], web_fetch["permission"])   # auto
        self.assertEqual(browser["scopes"], ["net.outbound"])
        self.assertFalse(browser["side_effect"])
        self.assertEqual(browser["source"], "code_agent")

    def test_dispatched_web_tools_all_have_specs(self) -> None:
        # Every dispatched web tool the model can name remains visible in inventory.
        for name in ("web_search", "web_fetch", "browser"):
            self.assertIn(name, self.specs)


if __name__ == "__main__":
    unittest.main()
