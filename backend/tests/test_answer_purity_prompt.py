from __future__ import annotations

import unittest

from app.application.code_agent import prompts


class AnswerPurityPromptTest(unittest.TestCase):
    """The answer-purity rule (verified vs guess, cite sources, honest 'not found')
    must be present in the system prompt — regression guard against dropping it."""

    def test_purity_rule_present(self):
        # Rule 16 was compressed (2026-07-03 guard-audit) — 4 verbose bullets → one
        # dense line; the load-bearing directives must survive the compression.
        t = prompts.BASE_SYSTEM_PROMPT_TEMPLATE
        self.assertIn("РАЗДЕЛЯЙ ПРОВЕРЕННОЕ И ДОГАДКИ", t)  # the answer-purity rule
        self.assertIn("с источником", t)                    # cite sources
        self.assertIn("не проверено", t)                    # unverified marking
        self.assertIn("источник не найден", t)              # honest not-found
        self.assertIn("никаких выдуманных", t)              # no fabrication

    def test_org_facts_web_grounding_rule_present(self):
        # rule 9б — factual org/person/domain data must be web-grounded, not memory
        t = prompts.BASE_SYSTEM_PROMPT_TEMPLATE
        self.assertIn("ФАКТЫ О РЕАЛЬНЫХ ОРГАНИЗАЦИЯХ", t)

    def test_local_catalog_absence_requires_structured_search(self):
        t = prompts.BASE_SYSTEM_PROMPT_TEMPLATE
        self.assertIn("ЛОКАЛЬНЫЕ ПРАЙСЫ И КАТАЛОГИ", t)
        self.assertIn("library_search", t)
        self.assertIn("head()", t)


if __name__ == "__main__":
    unittest.main()
