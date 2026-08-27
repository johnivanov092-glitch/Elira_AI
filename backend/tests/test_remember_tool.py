from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.application.code_agent.tools import tool_remember
from app.application.code_agent.prompts import _build_system_prompt


class RememberToolTest(unittest.TestCase):
    """remember() persists a durable user fact/correction as the source of truth."""

    def test_correction_stored_as_authoritative(self):
        with patch("app.application.memory.add_fact", return_value={"ok": True, "id": 1}) as m, \
             tempfile.TemporaryDirectory() as tmp:
            res = tool_remember(
                Path(tmp),
                fact="Столица QA — Тестбург",
                correction=True,
                replaces_id=41,
            )
        self.assertTrue(res["ok"])
        kw = m.call_args.kwargs
        self.assertEqual(kw["source"], "user_correction")
        self.assertEqual(kw["importance"], 10)
        self.assertEqual(kw["category"], "user_fact")
        self.assertEqual(kw["replaces_id"], 41)

    def test_plain_fact_source_user(self):
        with patch("app.application.memory.add_fact", return_value={"ok": True, "id": 2}) as m, \
             tempfile.TemporaryDirectory() as tmp:
            tool_remember(Path(tmp), fact="Пользователь предпочитает тёмную тему")
        kw = m.call_args.kwargs
        self.assertEqual(kw["source"], "user")
        self.assertEqual(kw["importance"], 8)

    def test_too_short_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = tool_remember(Path(tmp), fact="x")
        self.assertFalse(res["ok"])

    def test_memory_failure_is_graceful(self):
        with patch("app.application.memory.add_fact", side_effect=RuntimeError("db down")), \
             tempfile.TemporaryDirectory() as tmp:
            res = tool_remember(Path(tmp), fact="valid fact here")
        self.assertFalse(res["ok"])
        self.assertIn("Не удалось", res["text"])

class UserFactsInjectionTest(unittest.TestCase):
    """Relevant durable user facts are injected into the system prompt."""

    def test_user_facts_injected(self):
        fake = [
            {"text": "Столица QA — Тестбург", "source": "user_correction"},
            {"text": "Проект называется Elira", "source": "user"},
        ]
        with patch("app.application.memory.authoritative_facts", return_value=fake), \
             tempfile.TemporaryDirectory() as tmp:
            prompt = _build_system_prompt(Path(tmp))
        # The injected SECTION header (rule 8а also mentions the phrase, so match
        # the "--- " section marker, which is unique to the injection).
        self.assertIn("--- Факты от пользователя", prompt)
        self.assertIn("Столица QA — Тестбург", prompt)
        self.assertIn("[поправка]", prompt)              # correction is marked
        self.assertIn("Проект называется Elira", prompt)

    def test_no_section_when_no_user_facts(self):
        with patch("app.application.memory.authoritative_facts", return_value=[]), \
             tempfile.TemporaryDirectory() as tmp:
            prompt = _build_system_prompt(Path(tmp))
        self.assertNotIn("--- Факты от пользователя", prompt)


if __name__ == "__main__":
    unittest.main()
