from __future__ import annotations

import unittest
from pathlib import Path

from app.application.code_agent.niche_rules import select_niche_rules
from app.application.code_agent.prompts import _build_system_prompt


class NicheRulesSelectTest(unittest.TestCase):
    def test_ssh_task_matches(self) -> None:
        rules = select_niche_rules("создай себе ssh доступ и дай хост")
        self.assertEqual(len(rules), 1)
        self.assertIn("SSH-ДОСТУПА", rules[0])
        self.assertIn("EncodedCommand", rules[0])

    def test_specific_keywords_match(self) -> None:
        for t in ("проверь authorized_keys", "ssh-keygen новый ключ",
                  "найди id_ed25519", "глянь known_hosts"):
            self.assertTrue(select_niche_rules(t), t)

    def test_unrelated_task_no_match(self) -> None:
        for t in ("прочитай main.py", "запусти тесты", "создай лендинг",
                  "почисти кэш проекта", ""):
            self.assertEqual(select_niche_rules(t), [], t)

    def test_none_is_safe(self) -> None:
        self.assertEqual(select_niche_rules(None), [])


class NicheInjectionTest(unittest.TestCase):
    def test_ssh_task_injects_rule(self) -> None:
        prompt = _build_system_prompt(Path("."), task_text="настрой мне ssh доступ")
        self.assertIn("SSH-ДОСТУПА", prompt)
        self.assertIn("Ниша-правило", prompt)

    def test_normal_task_prompt_unchanged(self) -> None:
        # Canary-safety guarantee: a non-SSH task adds NO niche block, so the
        # prompt is byte-identical to the no-task build → zero extra tokens.
        base = _build_system_prompt(Path("."), task_text="")
        normal = _build_system_prompt(Path("."), task_text="прочитай файл main.py")
        self.assertEqual(base, normal)
        self.assertNotIn("Ниша-правило", normal)


if __name__ == "__main__":
    unittest.main()
