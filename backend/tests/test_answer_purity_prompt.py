from __future__ import annotations

import unittest

from app.application.code_agent import prompts


class AnswerPurityPromptTest(unittest.TestCase):
    """The answer-purity rule (verified vs guess, cite sources, honest 'not found')
    must be present in the system prompt — regression guard against dropping it."""

    def test_purity_rule_present(self):
        t = prompts.BASE_SYSTEM_PROMPT_TEMPLATE
        self.assertIn("ЧИСТОТА ОТВЕТА", t)
        self.assertIn("[источник:", t)                 # inline citation convention
        self.assertIn("по памяти, не проверено", t)     # unverified marking
        self.assertIn("источник не найден", t)          # honest not-found

    def test_org_facts_web_grounding_rule_present(self):
        # rule 9б — factual org/person/domain data must be web-grounded, not memory
        t = prompts.BASE_SYSTEM_PROMPT_TEMPLATE
        self.assertIn("ФАКТЫ О РЕАЛЬНЫХ ОРГАНИЗАЦИЯХ", t)


if __name__ == "__main__":
    unittest.main()
