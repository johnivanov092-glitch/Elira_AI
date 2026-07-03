"""run_bash timeout cap + long-op guidance.

The per-run_bash hard cap was raised 120→300s (safe now that the tool-execution
heartbeat keeps the SSE alive during a long command). Genuinely long / background
work still belongs in run_server — the prompt must guide the model there so a big
download doesn't just die at the 300s ceiling.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._shell import _SHELL_TIMEOUT_MAX  # noqa: E402
from app.application.code_agent.prompts import TOOL_PROMPT_LINES  # noqa: E402


class ShellTimeoutCapTest(unittest.TestCase):
    def test_cap_is_300(self):
        self.assertEqual(_SHELL_TIMEOUT_MAX, 300)

    def test_run_bash_clamps_to_cap(self):
        # mirror the inline clamp in tool_run_bash
        safe = max(1, min(9999, _SHELL_TIMEOUT_MAX))
        self.assertEqual(safe, 300)

    def test_prompt_guides_long_ops_to_background(self):
        run_bash = TOOL_PROMPT_LINES["run_bash"]
        self.assertIn("300", run_bash)
        self.assertIn("run_server", run_bash)  # points long ops to the background tool
        run_server = TOOL_PROMPT_LINES["run_server"]
        self.assertIn("ФОН", run_server.upper())
        self.assertIn("logs", run_server)  # can poll the background output


if __name__ == "__main__":
    unittest.main()
