from __future__ import annotations

import tempfile
import unittest

from app.application.code_agent.loop_helpers import _looks_like_intent_without_action
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.prompts import BASE_SYSTEM_PROMPT_TEMPLATE


def _drive(chat_fn, user_message, run_id):
    with tempfile.TemporaryDirectory() as tmp:
        return list(stream_code_agent(
            user_message=user_message, project_root=tmp, model="test-model",
            max_steps=6, chat_fn=chat_fn, run_id=run_id,
            approval_wait_seconds=0, auto_remember=False,
        ))


class IntentHeuristicTest(unittest.TestCase):
    def test_matches_forward_looking_intent_at_tail(self):
        # The exact shape that ended the real run on a preamble.
        self.assertTrue(_looks_like_intent_without_action(
            "Сначала прочитаю полные CSS и JS файлы, чтобы точно знать что добавить."))
        self.assertTrue(_looks_like_intent_without_action("Начну с CSS-улучшений."))
        self.assertTrue(_looks_like_intent_without_action("Ок. Теперь добавлю анимации в hero."))
        self.assertTrue(_looks_like_intent_without_action("Let me read the files first."))

    def test_does_not_match_genuine_answers(self):
        # Delivered result — past tense, no forward intent.
        self.assertFalse(_looks_like_intent_without_action(
            "Готово: добавил анимации, тесты зелёные."))
        # User-directed instruction (2nd person / infinitive) — not "I will…".
        self.assertFalse(_looks_like_intent_without_action(
            "Теперь можешь запустить приложение командой npm run dev."))
        self.assertFalse(_looks_like_intent_without_action("2 + 2 = 4."))
        self.assertFalse(_looks_like_intent_without_action(""))


class IntentGateLoopTest(unittest.TestCase):
    def test_intent_preamble_triggers_one_nudge_then_serves_real_answer(self):
        state = {"i": 0}

        def chat_fn(**kw):
            if not kw.get("tools"):
                return {"message": {"content": "fallback", "tool_calls": []}}
            state["i"] += 1
            if state["i"] == 1:   # narrates intent, no tool call — would stop early
                return {"message": {"content": "Сначала прочитаю файлы, чтобы понять что добавить.", "tool_calls": []}}
            return {"message": {"content": "Готово: добавил анимации, всё работает.", "tool_calls": []}}

        evs = _drive(chat_fn, "Добавь анимации на лендинг", "intent1")
        finals = [e for e in evs if e.get("type") == "final_response"]
        self.assertTrue(finals)
        self.assertIn("Готово", finals[-1]["text"])          # the nudged retry was served
        self.assertNotIn("Сначала прочитаю", finals[-1]["text"])  # not the preamble
        self.assertEqual(state["i"], 2)                       # exactly one nudge fired

    def test_genuine_answer_is_not_cut(self):
        state = {"i": 0}

        def chat_fn(**kw):
            if not kw.get("tools"):
                return {"message": {"content": "fallback", "tool_calls": []}}
            state["i"] += 1
            return {"message": {"content": "2 + 2 = 4.", "tool_calls": []}}

        evs = _drive(chat_fn, "Сколько будет два плюс два?", "intent2")
        finals = [e for e in evs if e.get("type") == "final_response"]
        self.assertIn("4", finals[-1]["text"])
        self.assertEqual(state["i"], 1)  # no intent -> no nudge, answered in one shot


class PromptGuardrailTest(unittest.TestCase):
    def test_large_file_and_act_rules_present(self):
        p = BASE_SYSTEM_PROMPT_TEMPLATE
        self.assertIn("КРУПНЫЕ ФАЙЛЫ", p)          # rule 17 (giant-write guardrail)
        self.assertIn("ДЕЙСТВУЙ", p)               # rule 18 (act, don't narrate)
        self.assertIn("edit_file", p)


if __name__ == "__main__":
    unittest.main()
