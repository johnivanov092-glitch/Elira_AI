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

    def test_auto_classifier_routes_infra(self):
        from app.application.chat.local_chat import classify_mode
        self.assertEqual(classify_mode("Настрой firewall на роутере MikroTik"), "Инфраструктура")
        self.assertEqual(classify_mode("Подключись по ssh и проверь systemctl status nginx"), "Инфраструктура")
        self.assertEqual(classify_mode("Просканируй сеть и найди хосты"), "Инфраструктура")
        self.assertEqual(classify_mode("Разбери RouterOS конфиг роутера"), "Инфраструктура")
        self.assertEqual(classify_mode("Настрой DNS и DHCP в подсети"), "Инфраструктура")

    def test_auto_classifier_priorities_hold(self):
        from app.application.chat.local_chat import classify_mode
        # code beats infra: a bug-fix that merely mentions ssh stays Инженерный
        self.assertEqual(classify_mode("почини баг в ssh_config.py"), "Инженерный")
        # plain chat stays neutral, not infra
        self.assertEqual(classify_mode("как настроение сегодня"), "Баланс")

    def test_legacy_mode_keeps_identity_and_task_guidance(self):
        from app.application.persona.service import build_persona_prompt
        from app.application.code_agent.task_guidance import task_guidance_blocks
        prompt = build_persona_prompt("Инфраструктура")
        self.assertEqual(prompt, build_persona_prompt("Баланс"))
        guidance = task_guidance_blocks({"runtime_control"})
        self.assertIn("typed health/inventory", guidance["runtime"])
        self.assertIn("разрешения определяет Workflow", guidance["work"])


if __name__ == "__main__":
    unittest.main()
