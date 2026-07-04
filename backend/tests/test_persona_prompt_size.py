"""Persona prompt size budget test.

The original tight budget (700 chars) was calibrated for small local models
(2B-7B) that drift on a 500+ token system prompt. Elira actually runs a 35B /
64k model (Qwen3.6-35B-A3B — see reference_ai_server), for which a per-mode
instruction of a few hundred tokens is negligible. So the modes now carry a real
packed instruction in their first sentence (Личный/Баланс/Инженерный were bare
labels before). Budget raised accordingly — still BOUNDED so an overlay can't
grow into an unbounded page that eats the window every turn.
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


# Bounded for a 35B/64k model: the ~604-char identity/mission wrapper + a packed
# per-mode instruction (the richest, Инженерный, is ~520 chars) → ~1125. Cap at
# 1300 keeps real room for guidance while still blocking unbounded page-long
# overlays (a full multi-section spec must live as docs, not the per-call prompt).
PROMPT_CHAR_BUDGET = 1300


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
