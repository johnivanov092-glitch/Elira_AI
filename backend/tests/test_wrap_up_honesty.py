"""The interrupted-run wrap-up must be HONESTY-first, not "list your accomplishments".

Root cause of the confabulated success/key: WRAP_UP_PROMPT asked the model to
"summarize what was done (files, commands, results)" with no tools — a leading
frame that pushes a weak model to manufacture accomplishments (and even fabricate
an SSH key) when the run actually failed. The rewritten prompt forbids claiming
"done"/"verified" without a tool result and forbids inventing keys/output.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.loop_helpers import WRAP_UP_PROMPT, _wrap_up_text  # noqa: E402


class WrapUpHonestyTest(unittest.TestCase):
    def test_reason_placeholder_still_formats(self):
        out = WRAP_UP_PROMPT.format(reason="near-duplicate loop")
        self.assertIn("near-duplicate loop", out)
        self.assertNotIn("{reason}", out)

    def test_prompt_forbids_unverified_claims(self):
        p = WRAP_UP_PROMPT.lower()
        # must forbid claiming verified/works without an actual check
        self.assertIn("проверено", p)
        self.assertIn("предположение", p)

    def test_prompt_forbids_fabricated_values(self):
        p = WRAP_UP_PROMPT.lower()
        # must forbid inventing keys/passwords/output
        self.assertIn("не придумывай", p)
        self.assertTrue("ключ" in p and "парол" in p)

    def test_prompt_requires_reporting_failures(self):
        p = WRAP_UP_PROMPT.lower()
        self.assertIn("провалил", p)

    def test_deterministic_fallback_when_llm_returns_empty(self):
        # If the wrap-up LLM call returns nothing usable, we must still produce a
        # factual call-log summary — never a blank or invented answer.
        def empty_chat(**_kwargs):
            return {"message": {"content": ""}}

        text = _wrap_up_text(
            empty_chat, "local-model", 8192,
            messages=[{"role": "user", "content": "task"}],
            call_log=["run_bash(ssh ...)", "run_bash(python ...)"],
            reason="near-duplicate run_bash loop",
        )
        self.assertIn("near-duplicate run_bash loop", text)
        self.assertIn("run_bash", text)


if __name__ == "__main__":
    unittest.main()
