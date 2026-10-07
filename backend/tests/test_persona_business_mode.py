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

    def test_business_tasks_do_not_switch_personality(self):
        from app.application.chat.local_chat import resolve_persona_mode
        self.assertEqual(
            resolve_persona_mode("Авто", "Составь коммерческое предложение для клиента"),
            "Баланс",
        )

    def test_legacy_mode_keeps_identity(self):
        from app.application.persona.service import build_persona_prompt
        prompt = build_persona_prompt("Деловой")
        self.assertEqual(prompt, build_persona_prompt("Баланс"))

    def test_full_overlay_preview_in_api(self):
        from app.core.persona_defaults import PROFILE_MODE_OVERLAYS
        overlay = PROFILE_MODE_OVERLAYS["Деловой"]
        self.assertIn("не подтверждено", overlay)
        self.assertIn("юрист", overlay)


if __name__ == "__main__":
    unittest.main()
