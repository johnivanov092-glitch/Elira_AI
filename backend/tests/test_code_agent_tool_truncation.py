"""Tests for tool-output truncation in the LLM-facing messages list.

Locally we don't pay for tokens, but `num_ctx` is a hard cap. A single
50KB tool output (e.g. `run_bash('pytest -v')` on a big project, or
`read_file('package-lock.json')`) would eat the entire context window
and start truncating the system prompt off the front. These tests pin
the smart-truncation behavior that prevents that.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import (  # noqa: E402
    TOOL_RESULT_LLM_LIMIT,
    _truncate_for_llm,
    stream_code_agent,
)


class TruncateForLlmTest(unittest.TestCase):
    """Pure unit tests for the truncation helper."""

    def test_small_text_passes_through_unchanged(self) -> None:
        text = "Edited foo.py (1 replacement)"
        self.assertEqual(_truncate_for_llm(text), text)

    def test_text_at_limit_passes_through(self) -> None:
        text = "a" * TOOL_RESULT_LLM_LIMIT
        self.assertEqual(_truncate_for_llm(text), text)

    def test_oversized_text_keeps_head_and_tail(self) -> None:
        # Build a recognizable head + middle + tail
        head = "HEADER_LINE_AT_START\n" + ("a" * 9000)
        middle = "z" * 40000   # 40KB of filler that should be dropped
        tail = ("b" * 4000) + "\nFOOTER_LINE_AT_END"
        text = head + middle + tail

        result = _truncate_for_llm(text)
        self.assertLess(len(result), len(text))
        # Both head AND tail must survive — that's the whole point
        self.assertIn("HEADER_LINE_AT_START", result)
        self.assertIn("FOOTER_LINE_AT_END", result)
        # Middle must be dropped
        self.assertNotIn("z" * 200, result)
        # There must be an explicit truncation marker so the LLM knows
        self.assertIn("truncated", result.lower())

    def test_truncation_marker_includes_byte_count(self) -> None:
        text = "x" * 50000
        result = _truncate_for_llm(text)
        # Marker should mention how many chars were cut so the agent
        # can decide whether to do a follow-up read_file with offset.
        import re
        match = re.search(r"truncated (\d+) chars", result)
        self.assertIsNotNone(match, f"no byte count in marker: {result[-200:]}")
        cut = int(match.group(1))
        self.assertGreater(cut, 30000)  # Should have cut a meaningful chunk

    def test_result_length_within_budget(self) -> None:
        text = "x" * 100_000
        result = _truncate_for_llm(text)
        # Truncated output must be reasonably close to the limit
        # (some slack for the marker text)
        self.assertLessEqual(len(result), TOOL_RESULT_LLM_LIMIT + 200)

    def test_custom_limit_respected(self) -> None:
        text = "y" * 5000
        result = _truncate_for_llm(text, limit=1000)
        self.assertLess(len(result), 5000)
        self.assertIn("truncated", result.lower())

    def test_empty_text_passes_through(self) -> None:
        self.assertEqual(_truncate_for_llm(""), "")


class StreamFeedsTruncatedToolOutputToLlmTest(unittest.TestCase):
    """End-to-end: a huge tool output must be truncated in the messages
    list that goes back to the LLM, but the visible event for the
    frontend follows its own (smaller) truncation."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        # Lay down a big file the agent will "read"
        self.big_file = self.root / "huge.txt"
        self.big_file.write_text("LINE_START\n" + ("X" * 80000) + "\nLINE_END\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_huge_tool_output_is_truncated_in_second_llm_call(self) -> None:
        """First step: agent calls read_file on a huge file. Second step:
        we capture the messages list and confirm the tool message is
        truncated to the LLM budget, not the full 80K."""
        seen_messages: list[list[dict[str, Any]]] = []

        responses = iter([
            # Step 1: model asks to read the big file
            {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "huge.txt"},
                        }
                    }],
                }
            },
            # Step 2: model produces final text
            {"message": {"content": "Got it.", "tool_calls": []}},
        ])

        def fake_chat(**kwargs):
            # Snapshot the messages list right before responding
            msgs = kwargs.get("messages", [])
            seen_messages.append([dict(m) for m in msgs])
            return next(responses)

        events = list(stream_code_agent(
            user_message="read huge.txt",
            project_root=self.root,
            chat_fn=fake_chat,
        ))

        # Find the tool result in the messages list that went into the
        # SECOND LLM call (step 2 — the model that produces the answer)
        self.assertGreaterEqual(len(seen_messages), 2, "fake_chat should have been called twice")
        second_call_messages = seen_messages[1]
        tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1, "exactly one tool result expected")
        tool_content = tool_msgs[0]["content"]
        # The original file is 80K chars; the truncated version must be
        # much smaller than that.
        self.assertLess(len(tool_content), TOOL_RESULT_LLM_LIMIT + 500)
        # And it must contain the explicit truncation marker so the
        # agent knows it didn't see everything.
        self.assertIn("truncated", tool_content.lower())
        # Run finished cleanly
        done = events[-1]
        self.assertTrue(done["ok"])

    def test_small_tool_output_passes_unchanged_to_llm(self) -> None:
        """write_file produces a tiny 'Created foo.py (N chars)' message.
        That must NOT be touched by truncation."""
        seen_messages: list[list[dict[str, Any]]] = []
        responses = iter([
            {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "write_file",
                            "arguments": {"path": "tiny.py", "content": "x = 1\n"},
                        }
                    }],
                }
            },
            {"message": {"content": "ok", "tool_calls": []}},
        ])

        def fake_chat(**kwargs):
            seen_messages.append([dict(m) for m in kwargs.get("messages", [])])
            return next(responses)

        events = list(stream_code_agent(
            user_message="create tiny.py",
            project_root=self.root,
            chat_fn=fake_chat,
        ))
        second_call = seen_messages[1]
        tool_msgs = [m for m in second_call if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        # The write_file tool returns something like "Created tiny.py (6 chars)"
        # — that's tiny, must pass through unchanged
        self.assertNotIn("truncated", tool_msgs[0]["content"].lower())
        # And the run finished
        self.assertTrue(events[-1]["ok"])


if __name__ == "__main__":
    unittest.main()
