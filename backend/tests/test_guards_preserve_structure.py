"""Regression test: identity_guard and provenance_guard must preserve
paragraph structure ('\\n\\n' between paragraphs) when rewriting.

Bug history: the original implementation did sentence-split + " ".join(),
which collapsed every paragraph break to a single space. End-users saw
"streaming text looks great → final text is a wall of run-on text".
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.identity_guard import guard_identity_response  # noqa: E402
from app.application.chat.provenance_guard import guard_provenance_response  # noqa: E402


class ProvenanceGuardStructureTest(unittest.TestCase):
    def test_clean_text_paragraphs_pass_through(self) -> None:
        # No banned phrases — text must come out unchanged (modulo whitespace
        # normalization). Critically, '\n\n' boundaries must survive.
        text = (
            "**Введение**\n\n"
            "Это первый абзац с нормальным контентом.\n\n"
            "**Заключение**\n\n"
            "Финальная мысль идёт здесь."
        )
        result = guard_provenance_response("расскажи мне о теме", text)
        self.assertIn("\n\n", result["text"])
        # Should preserve all four paragraphs
        self.assertGreaterEqual(result["text"].count("\n\n"), 3)

    def test_stripping_unwanted_keeps_other_paragraphs_separate(self) -> None:
        # One paragraph mentions banned token, others are fine — others
        # must remain on their own lines, not merged into a single blob.
        text = (
            "Первый абзац — обычный.\n\n"
            "Внутренняя справка: relevant user memory здесь.\n\n"
            "Третий абзац — тоже обычный."
        )
        result = guard_provenance_response("дай мне инфо", text)
        out = result["text"]
        # The banned middle paragraph may be stripped or rewritten, but the
        # first and third must still be on different lines.
        self.assertIn("\n\n", out)
        self.assertIn("Первый абзац", out)
        self.assertIn("Третий абзац", out)

    def test_long_structured_response_keeps_double_newlines(self) -> None:
        text = (
            "## Часть 1\n\n"
            "Описание первой части.\n\n"
            "## Часть 2\n\n"
            "Описание второй части.\n\n"
            "## Итог\n\n"
            "Итоговая мысль."
        )
        result = guard_provenance_response("объясни", text)
        # Must keep ≥5 '\n\n' boundaries (header / body / header / ...).
        self.assertGreaterEqual(result["text"].count("\n\n"), 5)


class IdentityGuardStructureTest(unittest.TestCase):
    def test_no_identity_drift_returns_unchanged(self) -> None:
        text = (
            "**Шаг 1**\n\n"
            "Сделать одно.\n\n"
            "**Шаг 2**\n\n"
            "Сделать другое."
        )
        result = guard_identity_response("что сделать?", text)
        # No model identity mention → guard returns original verbatim.
        self.assertEqual(result["text"], text)
        self.assertFalse(result["changed"])

    def test_drift_rewrite_preserves_other_paragraphs(self) -> None:
        # Paragraph 2 contains identity drift; paragraphs 1 and 3 should
        # remain as separate paragraphs after rewrite.
        text = (
            "Полезное содержимое в первом абзаце.\n\n"
            "Я gemma, языковая модель от Google.\n\n"
            "Третий абзац с полезным содержимым."
        )
        result = guard_identity_response("кто ты?", text)
        # Identity-question branch returns canonical reply — that's expected,
        # so test the non-question branch with a different prompt instead.

    def test_drift_in_non_identity_question_keeps_other_paragraphs(self) -> None:
        text = (
            "Первый полезный абзац с нормальным контентом.\n\n"
            "Я gemma, языковая модель.\n\n"
            "Третий полезный абзац с нормальным контентом."
        )
        # User asked about something else, but model drifted into self-talk
        result = guard_identity_response("объясни тему", text)
        out = result["text"]
        self.assertTrue(result["changed"])
        # The two surviving paragraphs must stay separated by '\n\n'.
        self.assertIn("Первый полезный абзац", out)
        self.assertIn("Третий полезный абзац", out)
        self.assertIn("\n\n", out)


if __name__ == "__main__":
    unittest.main()
