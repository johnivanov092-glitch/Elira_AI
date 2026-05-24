from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.prompting import (  # noqa: E402
    build_prompt as _build_prompt,
    wants_explicit_datetime_answer as _wants_explicit_datetime_answer,
)


def _noop_auto_skills(_input: str, **_kw: Any) -> str:
    return ""


class RuntimeDatetimePromptTest(unittest.TestCase):
    def test_explicit_datetime_detector_handles_common_queries(self) -> None:
        self.assertTrue(_wants_explicit_datetime_answer("Какая сегодня дата?"))
        self.assertTrue(_wants_explicit_datetime_answer("Который час сейчас"))
        self.assertFalse(_wants_explicit_datetime_answer("Привет, как дела?"))

    def test_regular_prompt_keeps_time_internal_only(self) -> None:
        prompt = _build_prompt(
            user_input="Привет",
            context_bundle="",
            run_auto_skills_func=_noop_auto_skills,
        )

        self.assertIn("ВНУТРЕННИЙ RUNTIME-КОНТЕКСТ", prompt)
        self.assertIn("Вопрос пользователя: Привет", prompt)
        self.assertNotIn("\n\nСейчас:", prompt)
        self.assertIn("НЕ упоминай дату, время", prompt)

    def test_direct_datetime_question_gets_precise_runtime_context(self) -> None:
        prompt = _build_prompt(
            user_input="Какая сегодня дата?",
            context_bundle="",
            run_auto_skills_func=_noop_auto_skills,
        )

        self.assertIn("Пользователь прямо спросил о дате или времени", prompt)
        self.assertIn("Текущая локальная дата и время:", prompt)
        self.assertIn("Вопрос пользователя: Какая сегодня дата?", prompt)


if __name__ == "__main__":
    unittest.main()
