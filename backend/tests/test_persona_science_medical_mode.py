"""Two new persona modes: 🧬 «Научный» (biology/physics/math) and 🩺 «Медицина».

Split per the user's choice. Both are precise (temp 0.2) and cite-or-refuse on
facts. classify_mode («Авто») must route science and health questions to them,
with the priority code > … > medical > science (a health question wins over a
biology word; a coding request still wins over both).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.local_chat import classify_mode  # noqa: E402
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
            self.assertIn("web_search", first)
            self.assertIn("не подтверждено", first)
            self.assertIn("не выдумывай", first)

    def test_medical_disclaimer_in_first_sentence(self):
        first = _first_sentence(PERSONA_MODES["Медицина"]["overlay"])
        self.assertTrue("не заменяет" in first and "врача" in first)

    def test_science_questions_route_to_научный(self):
        for q in (
            "докажи теорему Пифагора",
            "объясни как работает фермент в клетке",
            "выведи формулу через интеграл",
            "что такое квантовая запутанность",
            "как устроена молекула ДНК",
        ):
            self.assertEqual(classify_mode(q), "Научный", q)

    def test_health_questions_route_to_медицина(self):
        for q in (
            "что делать при высокой температуре и кашле",
            "какая дозировка парацетамола",
            "у меня болит голова второй день",
            "какие симптомы у этой болезни",
        ):
            self.assertEqual(classify_mode(q), "Медицина", q)

    def test_priority_code_beats_science(self):
        # a coding request that mentions a science term stays engineering
        self.assertEqual(classify_mode("напиши на python код для решения уравнения"), "Инженерный")

    def test_priority_medical_beats_science(self):
        # health context wins even with a biology word present
        self.assertEqual(classify_mode("какое лечение снижает воспаление в клетках"), "Медицина")


if __name__ == "__main__":
    unittest.main()
