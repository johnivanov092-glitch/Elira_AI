"""Tests for application/identity_guard and application/provenance_guard (pure Python)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.identity_guard.runtime import (  # noqa: E402
    guard_identity_response,
    is_identity_question,
)
from app.application.provenance_guard.runtime import (  # noqa: E402
    guard_provenance_response,
    is_provenance_question,
)


# ─────────────────────────────────────────────────────────────────────────────
# identity_guard — is_identity_question
# ─────────────────────────────────────────────────────────────────────────────

class IdentityQuestionDetectionTest(unittest.TestCase):
    def test_кто_ты_is_identity_question(self) -> None:
        self.assertTrue(is_identity_question("Кто ты?"))

    def test_как_тебя_зовут_is_identity_question(self) -> None:
        self.assertTrue(is_identity_question("Как тебя зовут?"))

    def test_представься_is_identity_question(self) -> None:
        self.assertTrue(is_identity_question("Представься, пожалуйста"))

    def test_non_identity_question_returns_false(self) -> None:
        self.assertFalse(is_identity_question("Какая сегодня погода?"))

    def test_empty_string_returns_false(self) -> None:
        self.assertFalse(is_identity_question(""))

    def test_none_treated_as_empty(self) -> None:
        self.assertFalse(is_identity_question(None))  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# identity_guard — guard_identity_response
# ─────────────────────────────────────────────────────────────────────────────

class GuardIdentityResponseTest(unittest.TestCase):
    def test_identity_question_locked_to_safe_reply(self) -> None:
        result = guard_identity_response(
            user_input="Кто ты?",
            answer_text="Я LLaMA 3 — большая языковая модель.",
            persona_name="Elira",
        )
        self.assertTrue(result["changed"])
        self.assertEqual(result["reason"], "identity_question_locked")
        self.assertIn("Elira", result["text"])
        self.assertTrue(result["identity_question"])

    def test_non_identity_question_unchanged_when_no_drift(self) -> None:
        result = guard_identity_response(
            user_input="Помоги с кодом",
            answer_text="Вот пример на Python: x = 1 + 2",
            persona_name="Elira",
        )
        self.assertFalse(result["changed"])
        self.assertIsNone(result["reason"])
        self.assertFalse(result["identity_question"])

    def test_drift_sentence_removed(self) -> None:
        drifted = "Я — большая языковая модель. Чем могу помочь сегодня?"
        result = guard_identity_response(
            user_input="Расскажи про Python",
            answer_text=drifted,
            persona_name="Elira",
        )
        self.assertTrue(result["changed"])
        self.assertNotIn("языковая модель", result["text"])

    def test_empty_answer_returns_unchanged(self) -> None:
        result = guard_identity_response(
            user_input="Что-то",
            answer_text="",
            persona_name="Elira",
        )
        self.assertFalse(result["changed"])
        self.assertEqual(result["text"], "")

    def test_custom_persona_name_used(self) -> None:
        result = guard_identity_response(
            user_input="Кто ты?",
            answer_text="Я GPT-4.",
            persona_name="Альфа",
        )
        self.assertIn("Альфа", result["text"])

    def test_result_has_required_keys(self) -> None:
        result = guard_identity_response("q", "a", "Elira")
        for key in ("text", "changed", "reason", "identity_question"):
            self.assertIn(key, result)

    def test_already_correct_safe_reply_not_changed(self) -> None:
        """If answer already equals the safe reply, changed should be False."""
        from app.application.identity_guard.runtime import _safe_identity_reply
        safe = _safe_identity_reply("Elira")
        result = guard_identity_response("Кто ты?", safe, "Elira")
        self.assertFalse(result["changed"])

    def test_llama_model_name_triggers_rewrite(self) -> None:
        result = guard_identity_response(
            user_input="Скажи пару слов о себе",
            answer_text="Я LLaMA и могу помочь с различными задачами.",
            persona_name="Elira",
        )
        self.assertTrue(result["changed"])


# ─────────────────────────────────────────────────────────────────────────────
# provenance_guard — is_provenance_question
# ─────────────────────────────────────────────────────────────────────────────

class ProvenanceQuestionDetectionTest(unittest.TestCase):
    def test_откуда_ты_знаешь_detected(self) -> None:
        self.assertTrue(is_provenance_question("Откуда ты знаешь это?"))

    def test_покажи_источники_detected(self) -> None:
        self.assertTrue(is_provenance_question("Покажи источники"))

    def test_give_sources_detected(self) -> None:
        self.assertTrue(is_provenance_question("give sources?"))

    def test_show_sources_detected(self) -> None:
        self.assertTrue(is_provenance_question("show sources please"))

    def test_non_provenance_question_returns_false(self) -> None:
        self.assertFalse(is_provenance_question("Как приготовить суп?"))

    def test_empty_returns_false(self) -> None:
        self.assertFalse(is_provenance_question(""))


# ─────────────────────────────────────────────────────────────────────────────
# provenance_guard — guard_provenance_response
# ─────────────────────────────────────────────────────────────────────────────

class GuardProvenanceResponseTest(unittest.TestCase):
    def test_raw_markers_stripped(self) -> None:
        answer = "[fact] Some important fact here."
        result = guard_provenance_response("", answer)
        self.assertNotIn("[fact]", result["text"])

    def test_memory_marker_stripped(self) -> None:
        answer = "[memory] User prefers dark mode."
        result = guard_provenance_response("", answer)
        self.assertNotIn("[memory]", result["text"])

    def test_provenance_question_rewrites_natural_response(self) -> None:
        answer = "Из моей памяти: ты любишь Python."
        result = guard_provenance_response("Откуда ты знаешь?", answer)
        self.assertTrue(result["provenance_question"])
        # The phrase gets rewritten away from "из моей памяти"
        self.assertNotIn("Из моей памяти", result["text"])

    def test_non_provenance_strips_technical_tokens(self) -> None:
        answer = "Ответ: relevant user memory это важно."
        result = guard_provenance_response("Помоги", answer)
        self.assertNotIn("relevant user memory", result["text"])

    def test_empty_answer_passes_through(self) -> None:
        result = guard_provenance_response("q", "")
        self.assertEqual(result["text"], "")
        self.assertFalse(result["changed"])

    def test_clean_answer_unchanged(self) -> None:
        answer = "Питон — это интерпретируемый язык программирования."
        result = guard_provenance_response("Расскажи про питон", answer)
        self.assertFalse(result["changed"])
        self.assertEqual(result["text"], answer)

    def test_result_has_required_keys(self) -> None:
        result = guard_provenance_response("q", "a")
        for key in ("text", "changed", "reason", "provenance_question"):
            self.assertIn(key, result)

    def test_меня_зовут_rewritten_for_name_question(self) -> None:
        result = guard_provenance_response(
            "Как меня зовут?",
            "Из моей памяти меня зовут Алиса.",
        )
        # Should rewrite personal-name form
        self.assertIn("Алиса", result["text"])


if __name__ == "__main__":
    unittest.main()
