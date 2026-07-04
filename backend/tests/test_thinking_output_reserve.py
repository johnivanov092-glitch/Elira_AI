"""Thinking mode must reserve more OUTPUT budget so a long chain-of-thought
never truncates the answer.

Live finding: reasoning_content and the answer share one output stream. With the
normal 4096 reserve (64k window) a long reasoning + answer near the context limit
made the server context-shift mid-generation and drop the answer ("думала, а
ответа нет"). thinking=True doubles the reserve (floor 8192, capped at a third of
the window) — the input budget shrinks instead.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.context import profile as profile_mod  # noqa: E402

_SCW = "app.infrastructure.llm.openai_compatible.server_context_window"


class ThinkingOutputReserveTest(unittest.TestCase):
    def test_thinking_enlarges_output_and_shrinks_input_64k(self):
        with mock.patch(_SCW, return_value=65536):
            base = profile_mod.get_active_context_profile("local-model", thinking=False)
            think = profile_mod.get_active_context_profile("local-model", thinking=True)
        # more room for the answer when reasoning is on...
        self.assertGreater(think["reserved_output_tokens"], base["reserved_output_tokens"])
        self.assertGreaterEqual(think["reserved_output_tokens"], 8192)
        # ...paid for by a smaller input budget, not by overflowing the window
        self.assertLess(think["safe_input_budget"], base["safe_input_budget"])
        self.assertGreater(think["safe_input_budget"], 0)
        self.assertTrue(think["thinking"])
        self.assertFalse(base["thinking"])

    def test_default_is_no_thinking_no_change(self):
        # Omitting thinking must behave exactly like the old profile (no regression).
        with mock.patch(_SCW, return_value=65536):
            default = profile_mod.get_active_context_profile("local-model")
            explicit_off = profile_mod.get_active_context_profile("local-model", thinking=False)
        self.assertEqual(default["reserved_output_tokens"], explicit_off["reserved_output_tokens"])
        self.assertEqual(default["reserved_output_tokens"], 4096)  # unchanged 64k default

    def test_reserve_capped_so_small_window_keeps_usable_input(self):
        with mock.patch(_SCW, return_value=8192):
            think = profile_mod.get_active_context_profile("local-model", thinking=True)
        # never more than ~a third of the window
        self.assertLessEqual(think["reserved_output_tokens"], max(2048, 8192 // 3))
        self.assertGreater(think["safe_input_budget"], 0)


if __name__ == "__main__":
    unittest.main()
