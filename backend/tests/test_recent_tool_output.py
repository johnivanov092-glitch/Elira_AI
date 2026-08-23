"""Verbatim recent-tool-output carry between turns."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.loop_helpers import (  # noqa: E402
    RECENT_TOOLS_PREFIX,
    _recent_tool_snippet,
    _recent_tools_digest,
)
from app.application.code_agent.history import _coerce_history  # noqa: E402


class RecentToolOutputTest(unittest.TestCase):
    def test_snippet_only_for_grounding_tools(self):
        self.assertIsNotNone(_recent_tool_snippet("read_file", "x.py", "содержимое файла"))
        self.assertIsNone(_recent_tool_snippet("todo_update", "", "что-то"))  # not grounding
        self.assertIsNone(_recent_tool_snippet("read_file", "x", "   "))       # empty

    def test_snippet_truncates_long_output(self):
        s = _recent_tool_snippet("read_file", "big.txt", "z" * 9000)
        self.assertIn("обрезано", s)
        self.assertLess(len(s), 3000)  # per-entry cap ~2500

    def test_digest_keeps_last_n_and_caps(self):
        entries = [f"### read_file(f{i})\n" + "z" * 100 for i in range(20)]
        d = _recent_tools_digest(entries)
        self.assertIn("f19", d)        # most recent kept
        self.assertNotIn("f0)", d)     # oldest dropped
        self.assertLessEqual(len(d), 9010)

    def test_empty_digest(self):
        self.assertEqual(_recent_tools_digest([]), "")

    def test_coerce_frames_recent_block_as_assistant_runtime_context(self):
        out = _coerce_history([
            {"role": "user", "content": "что там в файле?"},
            {"role": "assistant", "content": f"{RECENT_TOOLS_PREFIX}\n### read_file(x)\nреальный текст файла 777"},
        ])
        context_msgs = [m for m in out if m["role"] == "assistant"]
        self.assertTrue(any("реальный текст файла 777" in m["content"] for m in context_msgs))
        self.assertFalse(any(RECENT_TOOLS_PREFIX in m["content"] for m in out))
        self.assertFalse(any(m["role"] == "system" for m in out))


if __name__ == "__main__":
    unittest.main()
