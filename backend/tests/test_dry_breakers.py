"""DRY sequence breakers — regression for the IP-mutation bug.

Live: a network scan made the model walk IPs (192.168→192.169→192.166→192.170…)
and DNS servers (8.8.8.8→9.9.9.9→6.6.6.6…). The correct IP was in context; DRY
(anti-repetition) penalised the repeated octets and pushed the model to MUTATE
them. Adding "." (and other separators) to dry_sequence_breakers stops DRY treating
an IP/MAC/number as a penalizable repeat, while prose runaway is still caught.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import _ANTI_REPEAT_SAMPLING  # noqa: E402
from app.infrastructure.llm.openai_compatible import (  # noqa: E402
    _SAMPLING_EXTRA_KEYS,
    _apply_sampling_extra,
)


class DryBreakersTest(unittest.TestCase):
    def test_number_separators_are_breakers(self):
        brks = _ANTI_REPEAT_SAMPLING.get("dry_sequence_breakers")
        self.assertIsInstance(brks, list)
        for ch in (".", ":", "-", "/", "\n"):
            self.assertIn(ch, brks)

    def test_breakers_are_whitelisted(self):
        self.assertIn("dry_sequence_breakers", _SAMPLING_EXTRA_KEYS)

    def test_breakers_reach_the_request_body(self):
        payload: dict = {}
        _apply_sampling_extra(payload, {"sampling": dict(_ANTI_REPEAT_SAMPLING)})
        self.assertIn("dry_sequence_breakers", payload)
        self.assertIn(".", payload["dry_sequence_breakers"])
        # the core DRY params still pass through
        self.assertEqual(payload["dry_multiplier"], _ANTI_REPEAT_SAMPLING["dry_multiplier"])


if __name__ == "__main__":
    unittest.main()
