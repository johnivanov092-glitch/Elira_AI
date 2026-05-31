"""Tests for the compressed-context / summarize_history pathway.

Three behaviors pinned here:
  1. `_coerce_history` re-tags assistant turns with the `[CONTEXT SUMMARY]`
     prefix as `system` messages, so the LLM doesn't think it said
     them itself.
  2. `summarize_history` respects a total transcript cap and drops
     oldest turns first, with an explicit marker.
  3. `summarize_history` handles per-message + total caps together.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import (  # noqa: E402
    _coerce_history,
    summarize_history,
)


class CoerceHistoryReTagsSummaryTest(unittest.TestCase):
    def test_summary_assistant_message_becomes_system(self) -> None:
        history = [
            {"role": "user", "content": "task A"},
            {"role": "assistant", "content": "answer A"},
            {"role": "assistant", "content": "[CONTEXT SUMMARY]\n- did X\n- fixed Y"},
            {"role": "user", "content": "task B"},
        ]
        out = _coerce_history(history)
        roles = [m["role"] for m in out]
        # The summary turn must have flipped to 'system'
        self.assertEqual(roles, ["user", "assistant", "system", "user"])

    def test_summary_marker_is_stripped_and_context_added(self) -> None:
        history = [
            {"role": "assistant", "content": "[CONTEXT SUMMARY]\n- fact 1\n- fact 2"},
        ]
        out = _coerce_history(history)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["role"], "system")
        # The literal marker must NOT remain in the system content
        self.assertNotIn("[CONTEXT SUMMARY]", out[0]["content"])
        # The actual summary body must be present
        self.assertIn("fact 1", out[0]["content"])
        self.assertIn("fact 2", out[0]["content"])
        # Some kind of explanatory framing should be there
        self.assertIn("summary", out[0]["content"].lower())

    def test_empty_summary_marker_is_dropped(self) -> None:
        """A turn that is JUST the marker (no body) must be skipped, not
        rewritten into an empty system message."""
        history = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "[CONTEXT SUMMARY]\n"},
            {"role": "assistant", "content": "real answer"},
        ]
        out = _coerce_history(history)
        roles = [m["role"] for m in out]
        # Empty-marker assistant message dropped; real answer kept
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(out[1]["content"], "real answer")

    def test_user_with_summary_prefix_is_not_rewritten(self) -> None:
        """Defensive: only ASSISTANT-tagged messages should trigger the
        rewrite. A user who happens to type `[CONTEXT SUMMARY]` stays as
        a user."""
        history = [
            {"role": "user", "content": "[CONTEXT SUMMARY]\nfake"},
        ]
        out = _coerce_history(history)
        self.assertEqual(out[0]["role"], "user")
        self.assertIn("[CONTEXT SUMMARY]", out[0]["content"])


class SummarizeHistoryTranscriptCapTest(unittest.TestCase):
    def test_long_transcript_drops_oldest_with_marker(self) -> None:
        """Build a synthetic 60-turn transcript that vastly exceeds the
        30K cap; verify oldest are dropped and a marker is prepended."""
        captured: dict[str, Any] = {}

        def fake_chat(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            return {"message": {"content": "summary OK", "tool_calls": []}}

        # 60 turns alternating user/agent, each ~1500 chars → 90K total,
        # well above the 30K cap.
        big = "X" * 1500
        messages = []
        for i in range(30):
            messages.append({"role": "user", "content": f"task_{i}: " + big})
            messages.append({"role": "assistant", "content": f"answer_{i}: " + big})

        result = summarize_history(messages, chat_fn=fake_chat)
        self.assertTrue(result["ok"])
        self.assertEqual(result["turn_count"], 60)

        # Inspect what the summarizer ACTUALLY received
        sent_user_msg = captured["messages"][1]["content"]
        # Total transcript size sent must be reasonable — well below the
        # 90K input; the cap is 30K so allow some slack for marker text.
        self.assertLess(len(sent_user_msg), 50000)
        # Marker indicating dropped turns must be present
        self.assertIn("dropped", sent_user_msg.lower())
        # The MOST RECENT turns must survive (e.g. task_29 / answer_29)
        self.assertIn("task_29", sent_user_msg)
        # The OLDEST turns must NOT (e.g. task_0)
        self.assertNotIn("task_0:", sent_user_msg)

    def test_short_transcript_no_drop_marker(self) -> None:
        captured: dict[str, Any] = {}

        def fake_chat(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            return {"message": {"content": "ok", "tool_calls": []}}

        messages = [
            {"role": "user", "content": "small task"},
            {"role": "assistant", "content": "small answer"},
            {"role": "user", "content": "follow-up"},
            {"role": "assistant", "content": "follow-up answer"},
        ]
        result = summarize_history(messages, chat_fn=fake_chat)
        self.assertTrue(result["ok"])
        sent = captured["messages"][1]["content"]
        # Short transcript should NOT trigger the drop marker
        self.assertNotIn("dropped", sent.lower())
        # All four turns must appear in chronological order
        idx_small = sent.find("small task")
        idx_follow = sent.find("follow-up")
        self.assertGreater(idx_small, -1)
        self.assertGreater(idx_follow, idx_small)

    def test_per_message_cap_truncates_individual_long_turn(self) -> None:
        """A single 20K-char agent answer must be capped at the per-message
        limit (4000 chars) BEFORE being added to the transcript."""
        captured: dict[str, Any] = {}

        def fake_chat(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            return {"message": {"content": "ok", "tool_calls": []}}

        big_answer = "Z" * 20000
        messages = [
            {"role": "user", "content": "ask"},
            {"role": "assistant", "content": big_answer},
        ]
        summarize_history(messages, chat_fn=fake_chat)
        sent = captured["messages"][1]["content"]
        # No single 'Z' block of 5000+ chars should survive
        self.assertNotIn("Z" * 5000, sent)
        # But the truncation marker [...] should be present
        self.assertIn("[...]", sent)

    def test_summary_re_tagged_turns_get_prior_summary_prefix(self) -> None:
        """If an earlier compression already lives in the history as a
        re-tagged system message, the summarizer must see it labelled
        as PRIOR_SUMMARY so it knows it's not a regular user/agent turn.
        """
        captured: dict[str, Any] = {}

        def fake_chat(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            return {"message": {"content": "ok", "tool_calls": []}}

        # The frontend would send this as assistant+marker; _coerce_history
        # converts it to system inside summarize_history.
        messages = [
            {"role": "assistant", "content": "[CONTEXT SUMMARY]\n- earlier did X"},
            {"role": "user", "content": "new task"},
            {"role": "assistant", "content": "new answer"},
        ]
        summarize_history(messages, chat_fn=fake_chat)
        sent = captured["messages"][1]["content"]
        self.assertIn("PRIOR_SUMMARY", sent)
        self.assertIn("earlier did X", sent)


if __name__ == "__main__":
    unittest.main()
