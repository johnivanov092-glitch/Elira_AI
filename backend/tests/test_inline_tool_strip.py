"""Display safety net: leaked <tool_call> markup must not show up as answer text.

Live: after the loop-guard fired, a degenerate model's wrap-up answer contained a
raw `<tool_call><function=run_bash>…</tool_call>` block that rendered as literal XML
in the chat. _strip_tool_call_markup removes it from any final answer.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.history import _coerce_history  # noqa: E402
from app.application.code_agent.inline_tool_calls import _strip_tool_call_markup  # noqa: E402


class StripToolCallMarkupTest(unittest.TestCase):
    def test_strips_full_block_keeps_prose(self):
        s = ("Итог: <tool_call><function=run_bash><parameter=command>ping -n 2 192"
             "</parameter><parameter=timeout>15</parameter></function></tool_call>")
        out = _strip_tool_call_markup(s)
        self.assertNotIn("<tool_call", out)
        self.assertNotIn("<function=", out)
        self.assertNotIn("ping -n 2", out)  # whole block gone, not just tags
        self.assertIn("Итог:", out)

    def test_leaked_screenshot_format_becomes_empty(self):
        s = ("<tool_call> <function=run_bash> <parameter=command> ping -n 2 -4 -w 300 192 "
             "</parameter> <parameter=timeout> 15 </parameter> </function> </tool_call>")
        self.assertEqual(_strip_tool_call_markup(s), "")

    def test_plain_text_unchanged(self):
        s = "В проекте 5 файлов, всё работает."
        self.assertEqual(_strip_tool_call_markup(s), s)

    def test_truncated_block_tags_removed(self):
        s = "текст <tool_call><function=run_bash> ping -n 1"  # degenerate, no closers
        out = _strip_tool_call_markup(s)
        self.assertNotIn("<tool_call", out)
        self.assertNotIn("<function=", out)

    def test_orphan_closing_tags_are_removed(self):
        s = (
            "Файл для скачивания:\n"
            "`D:\\AIWork\\Elira_AI`\n"
            "</parameter>\n</function>\n</tool_call>"
        )
        out = _strip_tool_call_markup(s)
        self.assertEqual(out, "Файл для скачивания:\n`D:\\AIWork\\Elira_AI`")

    def test_orphan_tool_tags_are_removed_from_prior_assistant_history(self):
        history = _coerce_history([
            {
                "role": "assistant",
                "content": "Имя файла: report.pdf\n</parameter></function></tool_call>",
            },
        ])
        self.assertEqual(history, [{"role": "assistant", "content": "Имя файла: report.pdf"}])

    def test_empty_and_none_safe(self):
        self.assertEqual(_strip_tool_call_markup(""), "")
        self.assertEqual(_strip_tool_call_markup(None), "")


if __name__ == "__main__":
    unittest.main()
