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
    _MAX_SUMMARY_CHARS,
    _SUMMARY_PREFIX,
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
        system_turns = [m for m in result if m["role"] == "system"]
        # There should be 2 system turns: original + summary
        self.assertEqual(len(system_turns), 2)
        summary_turn = next((m for m in system_turns if _SUMMARY_PREFIX in m["content"]), None)
        self.assertIsNotNone(summary_turn)
        self.assertIn("Bullet summary.", summary_turn["content"])

    def test_recent_pairs_preserved(self):
        result, _ = self._compact(num_turns=10, keep_pairs=4)
        non_system = [m for m in result if m["role"] != "system"]
        # Last 4 pairs = 8 messages
        self.assertEqual(len(non_system), 8)
        # Most recent user message should be in result
        self.assertIn("User message 9", non_system[-2]["content"])

    def test_was_compacted_true(self):
        _, compacted = self._compact()
        self.assertTrue(compacted)


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
        system_turns = [m for m in result if m["role"] == "system"]
        self.assertTrue(any(_SUMMARY_PREFIX in m["content"] for m in system_turns))

    def test_fallback_when_summarize_raises(self):
        def _raise(**kw):
            raise RuntimeError("boom")
        result, compacted = self._compact_fallback(fail_fn=_raise)
        self.assertTrue(compacted)
        self.assertTrue(any(_SUMMARY_PREFIX in m["content"] for m in result))

    def test_fallback_keeps_last_n_messages(self):
        result, _ = self._compact_fallback(fallback_keep=4)
        non_system = [m for m in result if m["role"] != "system"
                      and _FALLBACK_PLACEHOLDER not in m["content"]]
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
        # 2 original + 1 new summary = 3
        self.assertGreaterEqual(len(system_in_result), 3)
        self.assertTrue(any("Base prompt." in m["content"] for m in system_in_result))
        self.assertTrue(any("Prior summary." in m["content"] for m in system_in_result))

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
        summaries = [m for m in result if m.get("role") == "system" and _SUMMARY_PREFIX in m.get("content", "")]
        self.assertEqual(len(summaries), 1)
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
        huge_summary = "S" * (_MAX_SUMMARY_CHARS + 1_000)

        def huge_summary_fn(**_kwargs):
            return {"ok": True, "summary": huge_summary, "error": None, "turn_count": 0}

        result, _ = maybe_compact(
            [_SYSTEM] + _make_turns(10),
            num_ctx=999_999,
            model="m",
            chat_fn=None,
            summarize_fn=huge_summary_fn,
            threshold=0.0,
        )
        summary = next(m for m in result if _SUMMARY_PREFIX in m.get("content", ""))
        self.assertLessEqual(len(summary["content"]), len(_SUMMARY_PREFIX) + _MAX_SUMMARY_CHARS)
        self.assertIn("[summary truncated]", summary["content"])

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


class TestCompactionInAgentLoop(unittest.TestCase):
    """Verifies that stream_code_agent emits context_compacted event when threshold exceeded."""

    def test_stream_emits_context_compacted_event(self):
        from app.application.code_agent.agent_loop import stream_code_agent
        import tempfile

        big_content = "Y" * 5000
        responses = iter([
            {"message": {"content": "", "tool_calls": [{"function": {"name": "glob", "arguments": {"pattern": "*"}}}]}},
            {"message": {"content": "done.", "tool_calls": []}},
        ])

        def fake_chat(**kwargs):
            return next(responses)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Set threshold=0 to always compact; inject via mock
            with mock.patch(
                "app.application.context.compaction.maybe_compact",
                wraps=lambda msgs, *a, **kw: (msgs, True),  # always say compacted
            ):
                events = list(stream_code_agent(
                    user_message="go",
                    project_root=root,
                    chat_fn=fake_chat,
                    num_ctx=8192,
                ))

        compacted_events = [e for e in events if e.get("type") == "context_compacted"]
        self.assertGreaterEqual(len(compacted_events), 1)


if __name__ == "__main__":
    unittest.main()
