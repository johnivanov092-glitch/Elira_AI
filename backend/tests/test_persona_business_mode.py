from __future__ import annotations

import unittest


class BusinessModeTest(unittest.TestCase):
    """New persona mode «Деловой»: documents / counterparties / marketing / money."""

    def test_mode_registered_with_all_three_knobs(self):
        from app.core.persona_defaults import PERSONA_MODES
        mode = PERSONA_MODES.get("Деловой")
        self.assertIsNotNone(mode)
        self.assertIn("не подтверждено", mode["overlay"])      # counterparty grounding
        self.assertIn("(оценка)", mode["overlay"])             # honest numbers
        self.assertEqual(mode["temperature"], 0.3)
        self.assertEqual(mode["tools"], "full")                # web_search/file_gen needed

    def test_profiles_api_exposes_it(self):
        from app.application.persona.profiles import get_profiles
        names = [p["name"] for p in get_profiles()["profiles"]]
        self.assertIn("Деловой", names)
        # existing modes untouched
        for existing in ("Авто", "Личный", "Баланс", "Инженерный"):
            self.assertIn(existing, names)

    def test_auto_classifier_routes_business(self):
        from app.application.chat.local_chat import classify_mode
        self.assertEqual(classify_mode("Составь коммерческое предложение для клиента"), "Деловой")
        self.assertEqual(classify_mode("Проверь контрагента, вот БИН 123456789012"), "Деловой")
        self.assertEqual(classify_mode("Напиши оффер для лендинга"), "Деловой")
        self.assertEqual(classify_mode("Посчитай маржу по прайсу"), "Деловой")

    def test_auto_classifier_priorities_hold(self):
        from app.application.chat.local_chat import classify_mode
        # personal beats business («письмо маме» has no business marker at all,
        # but even with one, personal wins by order)
        self.assertEqual(classify_mode("Устала, поддержи меня"), "Личный")
        # code beats business
        self.assertEqual(classify_mode("почини баг в договоре.py"), "Инженерный")
        # plain chat stays neutral
        self.assertEqual(classify_mode("расскажи про погоду"), "Баланс")

    def test_mode_line_reaches_persona_prompt(self):
        # By design only the overlay's FIRST sentence reaches the model (the
        # persona prompt is kept compact); it must carry the business posture.
        from app.application.persona.service import build_persona_prompt
        prompt = build_persona_prompt("Деловой")
        self.assertIn("Режим работы: деловой", prompt)
        self.assertIn("готового документа", prompt)      # document-first posture
        self.assertIn("проверяемых источников", prompt)  # grounding cue

    def test_full_overlay_preview_in_api(self):
        from app.core.persona_defaults import PROFILE_MODE_OVERLAYS
        overlay = PROFILE_MODE_OVERLAYS["Деловой"]
        self.assertIn("не подтверждено", overlay)
        self.assertIn("юрист", overlay)


if __name__ == "__main__":
    unittest.main()
