"""Persona prompt size budget test.

Small local models (2B-7B) cannot reliably attend to a 500+ token system
prompt. We keep `persona_evolution` machinery (DB writes, trait accumulation)
but the per-call prompt has to stay compact, otherwise the model drifts.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.persona.service import build_persona_prompt  # noqa: E402


# Budget: ~150 tokens ≈ 600 chars on Russian (heavier tokens than English).
# We allow some slack (700) to absorb minor wording tweaks.
PROMPT_CHAR_BUDGET = 700


class PersonaPromptSizeTest(unittest.TestCase):
    def test_universal_profile_prompt_within_budget(self) -> None:
        prompt = build_persona_prompt("Универсальный", "qwen2.5:3b")
        self.assertLessEqual(
            len(prompt),
            PROMPT_CHAR_BUDGET,
            f"Persona prompt is {len(prompt)} chars, must be ≤ {PROMPT_CHAR_BUDGET}",
        )

    def test_programmer_profile_prompt_within_budget(self) -> None:
        prompt = build_persona_prompt("Программист", "qwen2.5:3b")
        self.assertLessEqual(len(prompt), PROMPT_CHAR_BUDGET)

    def test_prompt_contains_core_identity(self) -> None:
        prompt = build_persona_prompt("Универсальный", "qwen2.5:3b")
        self.assertIn("Elira", prompt)
        self.assertIn("Миссия", prompt)
        self.assertIn("Идентичность", prompt)

    def test_prompt_includes_task_context_when_provided(self) -> None:
        prompt = build_persona_prompt("Универсальный", "qwen2.5:3b", task_context="Текущая задача: рефакторинг.")
        self.assertIn("Текущая задача: рефакторинг.", prompt)


if __name__ == "__main__":
    unittest.main()
