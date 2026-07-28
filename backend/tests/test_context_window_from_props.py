"""Context window must come from the server's real /props n_ctx, not a stale config default.

Root cause of "the agent stops holding context after a few tool-heavy turns":
the model is LOADED at 64k (llama.cpp -c 65536) but /v1/models does not expose
the window, so the harness fell back to LLAMA_SERVER_CONTEXT_WINDOW (128k) and
sized prompts for ~120k input — twice the real window. The server then
truncates/errors past 64k, evicting the system prompt + early grounding
mid-run. Fix: read the authoritative window from /props first.
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


class ContextWindowFromPropsTest(unittest.TestCase):
    def test_props_window_wins_over_config_default(self):
        # /props says 64k; config default is 128k. Profile must adopt 64k.
        with mock.patch(
            "app.infrastructure.llm.openai_compatible.server_context_window",
            return_value=65536,
        ):
            prof = profile_mod.get_active_context_profile("local-model")
        self.assertEqual(prof["ctx_size"], 65536)
        self.assertEqual(prof["source"], "server_props")
        self.assertEqual(prof["mode"], "64k")

    def test_input_budget_sized_for_real_window_not_double(self):
        # The whole point: input budget must fit inside 64k, never assume 128k.
        with mock.patch(
            "app.infrastructure.llm.openai_compatible.server_context_window",
            return_value=65536,
        ):
            prof = profile_mod.get_active_context_profile("local-model")
        self.assertLess(prof["safe_input_budget"], 65536)
        # sanity: still a usable budget (~54k after reserves), not collapsed
        self.assertGreater(prof["safe_input_budget"], 40_000)

    def test_live_resolver_rejects_stale_env_when_props_unreachable(self):
        with mock.patch(
            "app.infrastructure.llm.openai_compatible.server_context_window",
            return_value=None,
        ), mock.patch.dict(
            "os.environ",
            {"LLAMA_SERVER_CONTEXT_WINDOW": "131072"},
        ):
            with self.assertRaises(profile_mod.ContextResolutionError):
                profile_mod.resolve_context_window(None, live=True, fresh=True)

    def test_legacy_ctx_size_cannot_override_live_server(self):
        with mock.patch(
            "app.infrastructure.llm.openai_compatible.server_context_window",
            return_value=131072,
        ):
            prof = profile_mod.get_active_context_profile("local-model", ctx_size=32768)
        self.assertEqual(prof["ctx_size"], 131072)
        self.assertEqual(prof["limiting_source"], "server")


if __name__ == "__main__":
    unittest.main()
