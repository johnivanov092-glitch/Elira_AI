from __future__ import annotations

import unittest


class InfraModeTest(unittest.TestCase):
    """New persona mode «Инфраструктура»: senior network / systems / server engineer."""

    def test_mode_registered_with_expert_posture(self):
        from app.core.persona_defaults import PERSONA_MODES
        mode = PERSONA_MODES.get("Инфраструктура")
        self.assertIsNotNone(mode)
        self.assertIn("ПО ФАКТАМ", mode["overlay"])        # diagnose from facts, not memory
        self.assertIn("runtime_control(itops_assets)", mode["overlay"])
        self.assertIn("typed", mode["overlay"])
        self.assertIn("режимом Workflow", mode["overlay"])
        self.assertNotIn("ask_user", mode["overlay"])
        self.assertNotIn("allowlist", mode["overlay"])
        self.assertEqual(mode["temperature"], 0.2)          # precise/deterministic
        self.assertEqual(mode["tools"], "full")             # ssh/run_bash/configs/web

    def test_profiles_api_exposes_it_without_touching_others(self):
        from app.application.persona.profiles import get_profiles
        names = [p["name"] for p in get_profiles()["profiles"]]
        self.assertIn("Инфраструктура", names)
        for existing in ("Авто", "Личный", "Баланс", "Инженерный", "Деловой"):
            self.assertIn(existing, names)

    def test_infrastructure_tasks_do_not_switch_personality(self):
        from app.application.chat.local_chat import resolve_persona_mode
        self.assertEqual(
            resolve_persona_mode("Авто", "Подключись по ssh и проверь systemctl status nginx"),
            "Баланс",
        )

    def test_legacy_mode_keeps_identity_and_task_guidance(self):
        from app.application.persona.service import build_persona_prompt
        from app.application.code_agent.task_guidance import task_guidance_blocks
        prompt = build_persona_prompt("Инфраструктура")
        self.assertEqual(prompt, build_persona_prompt("Баланс"))
        guidance = task_guidance_blocks({"itops_network_inventory"})
        self.assertIn("typed health/inventory", guidance["itops"])
        self.assertIn("разрешения определяет Workflow", guidance["work"])


if __name__ == "__main__":
    unittest.main()
