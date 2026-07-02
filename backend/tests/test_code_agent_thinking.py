from __future__ import annotations

import tempfile
import unittest

from app.application.code_agent.agent_loop import stream_code_agent


def _make_stream(captured: list[dict], *, reasoning: str = "", answer: str = "ANSWER"):
    """Fake event-stream chat fn: records the kwargs it is called with (so tests
    can assert the per-request options) and replays reasoning + answer the way the
    real provider's event stream does."""
    def stream_fn(**kw):
        captured.append(kw)
        if reasoning:
            yield {"type": "reasoning", "content": reasoning}
        yield {"type": "delta", "content": answer}
        yield {"type": "message", "response": {"message": {
            "content": answer, "reasoning_content": reasoning, "tool_calls": [],
        }}}
    return stream_fn


def _drive(*, thinking: bool, reasoning: str, run_id: str) -> tuple[list[dict], list[dict]]:
    captured: list[dict] = []
    stream_fn = _make_stream(captured, reasoning=reasoning)
    with tempfile.TemporaryDirectory() as tmp:
        events = list(stream_code_agent(
            user_message="вопрос",
            project_root=tmp,
            model="test-model",
            max_steps=3,
            chat_fn=lambda **_: {},          # never used (streaming path), but keeps
            chat_stream_fn=stream_fn,        # the provider off the network
            run_id=run_id,
            approval_wait_seconds=0,
            auto_remember=False,
            thinking=thinking,
        ))
    return captured, events


class CodeAgentThinkingTest(unittest.TestCase):
    def test_thinking_on_passes_flag_and_streams_reasoning_apart(self):
        captured, events = _drive(thinking=True, reasoning="Шаг рассуждения.", run_id="think-on")
        # The per-request enable_thinking flag reached the provider options.
        self.assertEqual(
            captured[0]["options"].get("chat_template_kwargs"),
            {"enable_thinking": True},
        )
        # DRY anti-repetition rides along on every run (see below for non-think).
        self.assertEqual(captured[0]["options"].get("sampling", {}).get("dry_multiplier"), 0.8)
        # Reasoning surfaced on its own event, never folded into the answer.
        reasoning_evs = [e for e in events if e.get("type") == "reasoning_delta"]
        self.assertTrue(reasoning_evs)
        self.assertIn("Шаг рассуждения.", "".join(e["text"] for e in reasoning_evs))
        finals = [e for e in events if e.get("type") == "final_response"]
        self.assertTrue(finals)
        self.assertEqual(finals[-1]["text"], "ANSWER")
        self.assertNotIn("Шаг рассуждения.", finals[-1]["text"])

    def test_thinking_off_omits_flag_and_emits_no_reasoning(self):
        captured, events = _drive(thinking=False, reasoning="", run_id="think-off")
        self.assertNotIn("chat_template_kwargs", captured[0]["options"])
        # DRY anti-repetition now applies to EVERY run — a live non-think run
        # degenerated into a ×20-repeated paragraph in the answer channel.
        self.assertEqual(captured[0]["options"].get("sampling", {}).get("dry_multiplier"), 0.8)
        self.assertFalse([e for e in events if e.get("type") == "reasoning_delta"])
        finals = [e for e in events if e.get("type") == "final_response"]
        self.assertEqual(finals[-1]["text"], "ANSWER")


if __name__ == "__main__":
    unittest.main()
