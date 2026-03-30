from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.agents_service import _build_prompt, _wants_explicit_datetime_answer  # noqa: E402


class RuntimeDatetimePromptTest(unittest.TestCase):
    def test_explicit_datetime_detector_handles_common_queries(self) -> None:
        self.assertTrue(_wants_explicit_datetime_answer("Какая сегодня дата?"))
        self.assertTrue(_wants_explicit_datetime_answer("Который час сейчас"))
        self.assertFalse(_wants_explicit_datetime_answer("Привет, как дела?"))

    def test_regular_prompt_keeps_time_internal_only(self) -> None:
        with patch("app.services.agents_service._run_auto_skills", return_value=""):
            prompt = _build_prompt("Привет", "")

        self.assertIn("ВНУТРЕННИЙ RUNTIME-КОНТЕКСТ", prompt)
        self.assertIn("Вопрос пользователя: Привет", prompt)
        self.assertNotIn("\n\nСейчас:", prompt)
        self.assertIn("НЕ упоминай дату, время", prompt)

    def test_direct_datetime_question_gets_precise_runtime_context(self) -> None:
        with patch("app.services.agents_service._run_auto_skills", return_value=""):
            prompt = _build_prompt("Какая сегодня дата?", "")

        self.assertIn("Пользователь прямо спросил о дате или времени", prompt)
        self.assertIn("Текущая локальная дата и время:", prompt)
        self.assertIn("Вопрос пользователя: Какая сегодня дата?", prompt)


if __name__ == "__main__":
    unittest.main()
