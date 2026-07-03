"""Shared truncation — regression for the drifted-copy bug.

ssh_provider's `_truncate_for_llm` had silently become a head-only cut that dropped
the exit code / last error at the bottom of a command's output, while every other
copy kept the head AND tail. Consolidated into app.infrastructure.text; these tests
lock the head+tail behaviour and that the old call sites alias the one function.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.infrastructure.text import truncate_head, truncate_middle  # noqa: E402


class TruncateTest(unittest.TestCase):
    def test_middle_keeps_head_and_tail(self):
        text = "HEAD_MARK" + "x" * 20000 + "TAIL_MARK"
        out = truncate_middle(text, 1000)
        self.assertTrue(out.startswith("HEAD_MARK"))
        self.assertTrue(out.endswith("TAIL_MARK"))  # the exit-code-at-bottom case
        self.assertIn("truncated", out)
        self.assertLess(len(out), 1200)

    def test_middle_short_unchanged(self):
        self.assertEqual(truncate_middle("short output", 1000), "short output")

    def test_head_only(self):
        out = truncate_head("A" * 5000, 100)
        self.assertTrue(out.startswith("A" * 100))
        self.assertIn("truncated", out)

    def test_old_call_sites_alias_the_shared_fn(self):
        from app.application.code_agent.tools._sandbox import _truncate_middle
        from app.application.terminal.runtime import truncate_middle as tm_terminal
        from app.application.code_agent.sandbox import _truncate as sandbox_truncate
        self.assertIs(_truncate_middle, truncate_middle)
        self.assertIs(tm_terminal, truncate_middle)
        self.assertIs(sandbox_truncate, truncate_head)

    def test_ssh_truncate_keeps_tail_now(self):
        from app.application.tool_providers.ssh_provider import _truncate_for_llm
        text = "start" + "y" * 50000 + "exit=1"
        out = _truncate_for_llm(text, 800)
        self.assertTrue(out.endswith("exit=1"))  # the fix: tail no longer dropped


if __name__ == "__main__":
    unittest.main()
