"""Tests — Context compaction (P3 Шаг 8).

Verifies:
1. No compaction when below threshold.
2. Compaction triggered above threshold → was_compacted=True.
3. Summary turn inserted + recent pairs kept.
4. Deterministic fallback when summarize_fn fails.
5. Deterministic fallback when summarize_fn returns ok=False.
6. System messages are always preserved.
7. Nothing to summarize (only recent messages) → simple truncation.
8. Compaction called inside agent loop at 70% threshold.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.context.compaction import (  # noqa: E402
    _FALLBACK_PLACEHOLDER,
    _SUMMARY_PREFIX,
    _summary_char_budget,
    extract_rolling_summary,
    maybe_compact,
)

_SYSTEM = {"role": "system", "content": "You are Elira."}
_DUMMY_SUMMARIZE_OK = lambda messages, model, num_ctx, chat_fn: {
    "ok": True, "summary": "Bullet summary.", "error": None, "turn_count": len(messages),
}
_DUMMY_SUMMARIZE_FAIL = lambda messages, model, num_ctx, chat_fn: {
    "ok": False, "summary": "", "error": "model unavailable", "turn_count": 0,
}


def _make_turns(n: int) -> list[dict[str, Any]]:
    """Create n user/assistant pairs."""
    turns = []
    for i in range(n):
        turns.append({"role": "user", "content": f"User message {i}"})
        turns.append({"role": "assistant", "content": f"Agent reply {i}"})
    return turns


def _big_messages(num_ctx: int, fraction: float = 0.80) -> list[dict[str, Any]]:
    """Build a message list whose token estimate exceeds fraction × num_ctx."""
    target_chars = int(num_ctx * fraction * 4)
    filler = "X" * target_chars
    return [_SYSTEM, {"role": "user", "content": filler}]


class TestMaybeCompactThreshold(unittest.TestCase):

    def test_no_compaction_below_threshold(self):
        messages = [_SYSTEM] + _make_turns(2)
        result, compacted = maybe_compact(
            messages, num_ctx=100_000, model="m", chat_fn=None,
            summarize_fn=_DUMMY_SUMMARIZE_OK,
        )
        self.assertFalse(compacted)
        self.assertIs(result, messages)

    def test_compaction_triggered_above_threshold(self):
        # 10 turns ≈ enough large content to exceed threshold at small num_ctx
        messages = [_SYSTEM]
        big_content = "A" * 1000
        for _ in range(20):
            messages.append({"role": "user",      "content": big_content})
            messages.append({"role": "assistant", "content": big_content})
        # num_ctx small enough that 20 × 2 × 1000 chars > 70%
        result, compacted = maybe_compact(
            messages, num_ctx=2000, model="m", chat_fn=None,
            summarize_fn=_DUMMY_SUMMARIZE_OK,
        )
        self.assertTrue(compacted)


class TestMaybeCompactSummary(unittest.TestCase):

    def _compact(self, num_turns: int = 10, keep_pairs: int = 4, **kwargs):
        messages = [_SYSTEM] + _make_turns(num_turns)
        # Force threshold=0 so compaction always runs
        return maybe_compact(
            messages, num_ctx=999_999, model="m", chat_fn=None,
            summarize_fn=_DUMMY_SUMMARIZE_OK,
            threshold=0.0,
            keep_pairs=keep_pairs,
            **kwargs,
        )

    def test_system_message_preserved(self):
        result, _ = self._compact()
        self.assertTrue(any(m["role"] == "system" and "Elira" in m["content"] for m in result))

    def test_summary_turn_inserted(self):
        result, _ = self._compact()
        # Exactly ONE system turn (base); the rolling summary is now an assistant
        # message so strict templates see a single leading system message.
        system_turns = [m for m in result if m["role"] == "system"]
        self.assertEqual(len(system_turns), 1)
        summary_turn = next((m for m in result if _SUMMARY_PREFIX in m.get("content", "")), None)
        self.assertIsNotNone(summary_turn)
        self.assertEqual(summary_turn["role"], "assistant")
        self.assertIn("Bullet summary.", summary_turn["content"])

    def test_extract_rolling_summary_returns_summary_body(self):
        result, _ = self._compact()
        self.assertEqual(extract_rolling_summary(result), "Bullet summary.")

    def test_recent_pairs_preserved(self):
        result, _ = self._compact(num_turns=10, keep_pairs=4)
        non_system = [m for m in result if m["role"] != "system"
                      and _SUMMARY_PREFIX not in m.get("content", "")]
        # Last 4 pairs = 8 messages (the assistant summary is excluded above)
        self.assertEqual(len(non_system), 8)
        # Most recent user message should be in result
        self.assertIn("User message 9", non_system[-2]["content"])

    def test_was_compacted_true(self):
        _, compacted = self._compact()
        self.assertTrue(compacted)

    def test_single_leading_system_after_compaction(self):
        # The whole point of moving the summary to an assistant message: strict
        # chat templates (Qwen) reject a 2nd/non-leading system message.
        result, _ = self._compact(num_turns=10, keep_pairs=3)
        system_indices = [i for i, m in enumerate(result) if m["role"] == "system"]
        self.assertEqual(len(system_indices), 1)   # exactly one system
        self.assertEqual(system_indices[0], 0)     # and it is first


class TestMaybeCompactFallback(unittest.TestCase):

    def _compact_fallback(self, fail_fn=_DUMMY_SUMMARIZE_FAIL, num_turns=10, fallback_keep=8):
        messages = [_SYSTEM] + _make_turns(num_turns)
        return maybe_compact(
            messages, num_ctx=999_999, model="m", chat_fn=None,
            summarize_fn=fail_fn,
            threshold=0.0,
            fallback_keep=fallback_keep,
        )

    def test_fallback_when_summarize_returns_fail(self):
        result, compacted = self._compact_fallback()
        self.assertTrue(compacted)
        # summary is now an assistant message — check across all roles
        self.assertTrue(any(_SUMMARY_PREFIX in m["content"] for m in result))

    def test_fallback_when_summarize_raises(self):
        def _raise(**kw):
            raise RuntimeError("boom")
        result, compacted = self._compact_fallback(fail_fn=_raise)
        self.assertTrue(compacted)
        self.assertTrue(any(_SUMMARY_PREFIX in m["content"] for m in result))

    def test_fallback_keeps_last_n_messages(self):
        result, _ = self._compact_fallback(fallback_keep=4)
        non_system = [m for m in result if m["role"] != "system"
                      and _FALLBACK_PLACEHOLDER not in m["content"]
                      and _SUMMARY_PREFIX not in m["content"]]
        self.assertLessEqual(len(non_system), 4)

    def test_original_system_preserved_in_fallback(self):
        result, _ = self._compact_fallback()
        self.assertTrue(any(
            m["role"] == "system" and "Elira" in m["content"]
            for m in result
        ))


class TestMaybeCompactEdgeCases(unittest.TestCase):

    def test_nothing_to_summarize_simple_truncation(self):
        """Only keep_pairs×2 or fewer non-system messages → no summarize call, just truncate."""
        messages = [_SYSTEM] + _make_turns(2)  # 4 non-system, keep_pairs=4 → nothing to summarize
        called = []
        def spy_fn(**kw):
            called.append(1)
            return {"ok": True, "summary": "x", "error": None, "turn_count": 0}

        result, compacted = maybe_compact(
            messages, num_ctx=999_999, model="m", chat_fn=None,
            summarize_fn=spy_fn,
            threshold=0.0,
            keep_pairs=4,
        )
        self.assertTrue(compacted)
        self.assertEqual(called, [], "summarize_fn should not be called when nothing to summarize")

    def test_multiple_system_messages_all_preserved(self):
        messages = [
            {"role": "system", "content": "Base prompt."},
            {"role": "system", "content": "Prior summary."},
        ] + _make_turns(10)
        result, _ = maybe_compact(
            messages, num_ctx=999_999, model="m", chat_fn=None,
            summarize_fn=_DUMMY_SUMMARIZE_OK,
            threshold=0.0,
        )
        system_in_result = [m for m in result if m["role"] == "system"]
        # Both base system messages preserved; the new summary is an assistant
        # message (not a 3rd system), so a strict template still sees system-first.
        self.assertEqual(len(system_in_result), 2)
        self.assertTrue(any("Base prompt." in m["content"] for m in system_in_result))
        self.assertTrue(any("Prior summary." in m["content"] for m in system_in_result))
        self.assertTrue(any(m["role"] == "assistant" and _SUMMARY_PREFIX in m.get("content", "") for m in result))

    def test_repeated_compaction_keeps_single_summary_turn(self):
        messages = [
            _SYSTEM,
            {"role": "system", "content": _SUMMARY_PREFIX + "Pending work: finish API.\nImportant files: a.py"},
        ] + _make_turns(10)
        result, compacted = maybe_compact(
            messages, num_ctx=999_999, model="m", chat_fn=None,
            summarize_fn=_DUMMY_SUMMARIZE_OK,
            threshold=0.0,
        )
        self.assertTrue(compacted)
        # Single rolling summary, now emitted as an assistant message (a legacy
        # system-role summary in the input is still recognised and merged).
        summaries = [m for m in result if _SUMMARY_PREFIX in m.get("content", "")]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["role"], "assistant")
        self.assertIn("Pending work: finish API.", summaries[0]["content"])
        self.assertIn("Important files: a.py", summaries[0]["content"])
        self.assertIn("Bullet summary.", summaries[0]["content"])

    def test_previous_summary_is_passed_to_summarizer(self):
        seen: dict[str, Any] = {}

        def spy_summary(**kwargs):
            seen["messages"] = kwargs["messages"]
            return {"ok": True, "summary": "New summary.", "error": None, "turn_count": len(kwargs["messages"])}

        messages = [
            _SYSTEM,
            {"role": "system", "content": _SUMMARY_PREFIX + "Pending work: keep this."},
        ] + _make_turns(10)
        result, _ = maybe_compact(
            messages, num_ctx=999_999, model="m", chat_fn=None,
            summarize_fn=spy_summary,
            threshold=0.0,
        )
        self.assertIn("Previous compacted summary", seen["messages"][0]["content"])
        summary = next(m for m in result if _SUMMARY_PREFIX in m.get("content", ""))
        self.assertIn("Pending work: keep this.", summary["content"])

    def test_summary_size_is_capped(self):
        num_ctx = 999_999
        summary_limit = _summary_char_budget(num_ctx)
        huge_summary = "S" * (summary_limit + 1_000)

        def huge_summary_fn(**_kwargs):
            return {"ok": True, "summary": huge_summary, "error": None, "turn_count": 0}

        result, _ = maybe_compact(
            [_SYSTEM] + _make_turns(10),
            num_ctx=num_ctx,
            model="m",
            chat_fn=None,
            summarize_fn=huge_summary_fn,
            threshold=0.0,
        )
        summary = next(m for m in result if _SUMMARY_PREFIX in m.get("content", ""))
        self.assertLessEqual(
            len(summary["content"]),
            len(_SUMMARY_PREFIX) + summary_limit,
        )
        self.assertIn("[summary truncated]", summary["content"])

    def test_summary_budget_scales_without_an_upper_cap(self):
        self.assertEqual(_summary_char_budget(65_536), 4_096)
        self.assertEqual(_summary_char_budget(131_072), 8_192)
        self.assertEqual(_summary_char_budget(1_048_576), 65_536)

    def test_deterministic_fallback_summarizes_tool_results(self):
        messages = [_SYSTEM]
        for i in range(6):
            messages.extend([
                {"role": "user", "content": f"step {i}"},
                {"role": "assistant", "content": "", "tool_calls": []},
                {"role": "tool", "name": "run_bash", "content": f"exit=0 output {i}"},
            ])
        result, _ = maybe_compact(
            messages,
            num_ctx=999_999,
            model="m",
            chat_fn=None,
            summarize_fn=_DUMMY_SUMMARIZE_FAIL,
            threshold=0.0,
            keep_pairs=1,
        )
        summary = next(m for m in result if _SUMMARY_PREFIX in m.get("content", ""))
        self.assertIn("Recent tool results:", summary["content"])
        self.assertIn("run_bash", summary["content"])


class TestDeepCompaction(unittest.TestCase):
    """F3: tool work reaches the summarizer; rolling summary evicts oldest."""

    def test_flatten_converts_tool_messages_to_text(self):
        from app.application.code_agent.agent_loop import _flatten_for_summary

        msgs = [
            {"role": "user", "content": "поправь баг"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "src/a.py"}}},
            ]},
            {"role": "tool", "name": "read_file", "content": "X" * 1000},
        ]
        flat = _flatten_for_summary(msgs)
        self.assertEqual(flat[0], msgs[0])  # user passes through
        self.assertIn("[tools called] read_file(src/a.py)", flat[1]["content"])
        self.assertTrue(flat[2]["content"].startswith("[tool result read_file] "))
        # tool excerpt capped at 400 chars (+ prefix)
        self.assertLessEqual(len(flat[2]["content"]), 400 + len("[tool result read_file] "))

    def test_maybe_compact_passes_tool_work_to_summarizer(self):
        seen: dict[str, Any] = {}

        def spy_summary(**kwargs):
            seen["messages"] = kwargs["messages"]
            return {"ok": True, "summary": "S.", "error": None, "turn_count": 0}

        from app.application.code_agent.agent_loop import _flatten_for_summary

        messages = [_SYSTEM, {"role": "user", "content": "задача"}]
        for i in range(6):
            messages.append({"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "run_bash", "arguments": {"command": f"pytest {i}"}}},
            ]})
            messages.append({"role": "tool", "name": "run_bash", "content": f"exit=1 step {i}"})

        _result, compacted = maybe_compact(
            messages, num_ctx=999_999, model="m", chat_fn=None,
            summarize_fn=spy_summary,
            threshold=0.0,
            keep_pairs=1,
            prepare_messages=_flatten_for_summary,
        )
        self.assertTrue(compacted)
        joined = "\n".join(m["content"] for m in seen["messages"])
        self.assertIn("[tools called] run_bash(pytest 0)", joined)
        self.assertIn("[tool result run_bash] exit=1 step 0", joined)

    def test_merge_summary_evicts_oldest_not_newest(self):
        from app.application.context.compaction import _merge_summary

        old = "OLD " * 1000   # ~4000 chars — fills the cap alone
        new = "NEW-FACTS " * 30
        limit = _summary_char_budget(65_536)
        merged = _merge_summary([old], new, limit=limit)
        self.assertIn("NEW-FACTS", merged)          # newest survives intact
        self.assertIn("[older summaries dropped]", merged)
        self.assertNotIn("OLD", merged)             # oldest evicted
        self.assertLessEqual(len(merged), limit)

    def test_merge_summary_keeps_all_when_under_cap(self):
        from app.application.context.compaction import _merge_summary

        merged = _merge_summary(["first block"], "second block", limit=4_096)
        self.assertEqual(merged, "first block\n\nsecond block")


if __name__ == "__main__":
    unittest.main()
