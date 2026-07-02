from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.application.code_agent.loop_helpers import (
    _normalized_fingerprint,
    _strip_think_blocks,
)
from app.infrastructure.llm import openai_compatible


def _llama_env() -> dict[str, str]:
    return {
        "LLAMA_SERVER_ENABLED": "true",
        "LLAMA_SERVER_BASE_URL": "http://ai-server:8000/v1",
        "LLAMA_SERVER_MODEL": "local-model",
        "LLAMA_SERVER_TIMEOUT_SECONDS": "12",
    }


class _Response:
    def __init__(self, lines):
        self._lines = lines
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_lines(self, decode_unicode=False):
        yield from self._lines

    def close(self):
        self.closed = True


class ContentRunawayGuardTest(unittest.TestCase):
    """A degenerate ×N-paragraph answer is cut, collapsed and flagged."""

    def test_repeated_paragraph_is_cut_and_collapsed(self):
        para = "Да, вот. Вот это вот, я думаю, что это будет как-то так, да-да.\n"
        # 12 chunks × ~500 chars — crosses the 2000-char checkpoints with the
        # same paragraph repeating → detector fires well before the hard cap.
        chunk = para * 8
        lines = [
            "data: " + json.dumps(
                {"choices": [{"delta": {"content": chunk}}]}, ensure_ascii=False)
            for _ in range(12)
        ] + ["data: [DONE]"]
        response = _Response(lines)
        with patch.dict(os.environ, _llama_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
            return_value=response,
        ):
            events = list(openai_compatible.chat_completion_event_stream(
                model="local-model",
                messages=[{"role": "user", "content": "q"}],
                options={"num_ctx": 131_072},
            ))
        final = events[-1]["response"]
        self.assertTrue(final["content_runaway"])
        content = final["message"]["content"]
        # collapsed: the paragraph survives ~once, not ×96
        self.assertLess(content.count("Да, вот."), 3)
        self.assertIn("зацикливаться", content)
        self.assertTrue(response.closed)

    def test_normal_long_answer_not_cut(self):
        # 30 DISTINCT paragraphs — long but healthy → no flag, nothing collapsed.
        lines = [
            "data: " + json.dumps(
                {"choices": [{"delta": {"content": f"Пункт {i}: содержательный уникальный абзац номер {i} с деталями.\n"}}]},
                ensure_ascii=False)
            for i in range(30)
        ] + ["data: [DONE]"]
        response = _Response(lines)
        with patch.dict(os.environ, _llama_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
            return_value=response,
        ):
            events = list(openai_compatible.chat_completion_event_stream(
                model="local-model",
                messages=[{"role": "user", "content": "q"}],
                options={"num_ctx": 131_072},
            ))
        final = events[-1]["response"]
        self.assertFalse(final["content_runaway"])
        self.assertIn("Пункт 29", final["message"]["content"])


class ThinkStripperTest(unittest.TestCase):
    def test_strips_closed_and_unterminated_blocks(self):
        self.assertEqual(
            _strip_think_blocks("до <think>мысли</think> после").strip(), "до  после".strip())
        # unterminated tail (cut-off generation) is stripped too
        self.assertEqual(_strip_think_blocks("ответ <think>обры").strip(), "ответ")
        # no think markup → untouched
        self.assertEqual(_strip_think_blocks("чистый ответ"), "чистый ответ")


class FingerprintNormalizationTest(unittest.TestCase):
    def test_whitespace_variants_share_fingerprint(self):
        a = _normalized_fingerprint("run_bash", {"command": "pytest  -q"})
        b = _normalized_fingerprint("run_bash", {"command": "pytest -q "})
        c = _normalized_fingerprint("run_bash", {"command": "pytest\n-q"})
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_genuinely_different_args_differ(self):
        a = _normalized_fingerprint("read_file", {"path": "a.py", "offset": 100})
        b = _normalized_fingerprint("read_file", {"path": "a.py", "offset": 200})
        self.assertNotEqual(a, b)  # sequential chunk reads stay distinct


class CyrillicEstimatorTest(unittest.TestCase):
    def test_cyrillic_counts_denser_than_ascii(self):
        ru = openai_compatible._estimate_tokens("привет" * 100)
        en = openai_compatible._estimate_tokens("privet" * 100)
        self.assertGreater(ru, en)

    def test_guard_reserves_output_budget_when_no_max_tokens(self):
        # prompt ~fits alone, but with the default output reserve it must not.
        messages = [{"role": "user", "content": "x" * 4000}]  # ~1000 tokens
        with self.assertRaises(RuntimeError):
            openai_compatible._guard_context_request(
                messages, max_tokens=None, requested_ctx=1500,
            )


class ReasoningRunawayBudgetTest(unittest.TestCase):
    """Two runaway generations in one run → force-finalize (loop_guard)."""

    def test_second_runaway_finalizes_run(self):
        from app.application.code_agent.agent_loop import stream_code_agent
        calls = {"n": 0}

        def chat_fn(**kw):
            if not kw.get("tools"):
                return {"message": {"content": "wrap-up summary", "tool_calls": []}}
            calls["n"] += 1
            # every tool-enabled call reports a runaway cut with a tool call so
            # the loop keeps going instead of finalizing on the answer
            return {
                "message": {"content": "", "tool_calls": [{
                    "function": {"name": "todo_update", "arguments": {"items": [f"i{calls['n']}"]}},
                }]},
                "reasoning_runaway": True,
            }

        with tempfile.TemporaryDirectory() as tmp:
            events = list(stream_code_agent(
                user_message="задача", project_root=tmp, model="test-model",
                max_steps=10, chat_fn=chat_fn, run_id="runaway-budget",
                approval_wait_seconds=0, auto_remember=False,
            ))
        done = [e for e in events if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "loop_guard")
        self.assertIn("reasoning runaway", str(done.get("error")))
        self.assertLessEqual(calls["n"], 3)  # stopped at the budget, not max_steps


if __name__ == "__main__":
    unittest.main()
