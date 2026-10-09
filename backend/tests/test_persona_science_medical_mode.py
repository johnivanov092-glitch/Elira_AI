"""Legacy scientific/medical guidance keeps the one live personality."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.local_chat import resolve_persona_mode  # noqa: E402
from app.core.persona_defaults import PERSONA_MODES  # noqa: E402


def _first_sentence(overlay: str) -> str:
    # what actually reaches the model (_short_profile_line splits on ".")
    return overlay.split(".")[0].lower()


class ScienceMedicalModeTest(unittest.TestCase):
    def test_both_modes_registered_with_expected_shape(self):
        for name, icon in (("Научный", "🧬"), ("Медицина", "🩺")):
            self.assertIn(name, PERSONA_MODES)
            mode = PERSONA_MODES[name]
            self.assertEqual(mode["ui"]["icon"], icon)
            self.assertEqual(mode["temperature"], 0.2)  # precise, not creative
            self.assertEqual(mode["tools"], "full")

    def test_cite_or_refuse_is_in_the_line_the_model_sees(self):
        for name in ("Научный", "Медицина"):
            first = _first_sentence(PERSONA_MODES[name]["overlay"])
            self.assertIn("web-research", first)
            self.assertIn("не подтверждено", first)
            self.assertIn("не выдумывай", first)

    def test_medical_disclaimer_in_first_sentence(self):
        first = _first_sentence(PERSONA_MODES["Медицина"]["overlay"])
        self.assertTrue("не заменяет" in first and "врача" in first)

    def test_science_and_medical_tasks_do_not_switch_personality(self):
        for text in ("докажи теорему Пифагора", "у меня болит голова второй день"):
            self.assertEqual(resolve_persona_mode("Авто", text), "Баланс")

    def test_thin_modes_now_carry_instruction_not_a_bare_label(self):
        # Личный/Баланс/Инженерный used to send only "Режим работы: X" to the model.
        # Their first (model-visible) sentence must now pack real guidance.
        from app.application.persona.service import _short_profile_line
        for mode in ("Личный", "Баланс", "Инженерный"):
            line = _short_profile_line(mode)
            self.assertGreater(len(line), 120, mode)  # not just a label
            self.assertNotEqual(line.strip().rstrip("."), f"Режим работы: {mode.lower()}")


if __name__ == "__main__":
    unittest.main()
