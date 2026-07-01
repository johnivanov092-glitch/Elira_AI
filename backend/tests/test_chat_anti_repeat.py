from __future__ import annotations

import tempfile
import unittest

from app.application.code_agent.loop_helpers import _looks_like_repeat_request, _norm_answer
from app.application.code_agent.agent_loop import stream_code_agent


def _drive(chat_fn, user_message, history, run_id):
    with tempfile.TemporaryDirectory() as tmp:
        return list(stream_code_agent(
            user_message=user_message, project_root=tmp, model="test-model",
            max_steps=6, chat_fn=chat_fn, run_id=run_id,
            conversation_history=history, approval_wait_seconds=0, auto_remember=False,
        ))


class ChatAntiRepeatTest(unittest.TestCase):
    def test_repeat_request_markers(self):
        self.assertTrue(_looks_like_repeat_request("еще раз"))
        self.assertTrue(_looks_like_repeat_request("Повтори, пожалуйста"))
        self.assertFalse(_looks_like_repeat_request("дай источник"))

    def test_norm_answer_collapses_whitespace(self):
        self.assertEqual(_norm_answer("  a   b\n c "), "a b c")
        self.assertEqual(_norm_answer("x"), _norm_answer(" x  "))

    def test_echo_of_prev_answer_triggers_one_retry(self):
        history = [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "ANSWER A"}]
        state = {"i": 0}

        def chat_fn(**kw):
            if not kw.get("tools"):
                return {"message": {"content": "fallback", "tool_calls": []}}
            state["i"] += 1
            if state["i"] == 1:   # would echo the previous turn's answer verbatim
                return {"message": {"content": "ANSWER A", "tool_calls": []}}
            return {"message": {"content": "ANSWER B new", "tool_calls": []}}  # retry: fresh

        evs = _drive(chat_fn, "Совсем другой вопрос Q2", history, "rrep1")
        finals = [e for e in evs if e.get("type") == "final_response"]
        self.assertTrue(finals)
        self.assertIn("ANSWER B new", finals[-1]["text"])  # the nudged retry was served
        self.assertEqual(state["i"], 2)                     # exactly one retry fired

    def test_no_retry_when_answer_differs(self):
        history = [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "ANSWER A"}]
        state = {"i": 0}

        def chat_fn(**kw):
            if not kw.get("tools"):
                return {"message": {"content": "fallback", "tool_calls": []}}
            state["i"] += 1
            return {"message": {"content": "totally different answer", "tool_calls": []}}

        _drive(chat_fn, "Q2", history, "rrep2")
        self.assertEqual(state["i"], 1)  # answer differed -> no retry

    def test_explicit_repeat_request_allows_identical_answer(self):
        history = [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "ANSWER A"}]
        state = {"i": 0}

        def chat_fn(**kw):
            if not kw.get("tools"):
                return {"message": {"content": "fallback", "tool_calls": []}}
            state["i"] += 1
            return {"message": {"content": "ANSWER A", "tool_calls": []}}  # dup, but user asked to repeat

        evs = _drive(chat_fn, "повтори ещё раз", history, "rrep3")
        finals = [e for e in evs if e.get("type") == "final_response"]
        self.assertIn("ANSWER A", finals[-1]["text"])
        self.assertEqual(state["i"], 1)  # legit repeat request -> gate did not fire


if __name__ == "__main__":
    unittest.main()
