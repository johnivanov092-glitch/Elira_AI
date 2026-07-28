from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.context.profile import get_active_context_profile  # noqa: E402
from app.application.context.usage import get_context_usage  # noqa: E402


class ContextProfileTest(unittest.TestCase):
    def test_reads_live_server_context(self) -> None:
        # Adaptive contract: /props is the ONLY live source (never /models —
        # it omits the window on llama.cpp). The live value wins over config.
        cfg = SimpleNamespace(context_window=16384, model="local-model", base_url="http://server/v1")
        with patch("app.infrastructure.llm.openai_compatible.local_llm_config", return_value=cfg), patch(
            "app.infrastructure.llm.openai_compatible.server_context_window",
            return_value=131072,
        ):
            profile = get_active_context_profile("local-model")
        self.assertEqual(profile["ctx_size"], 131072)
        self.assertEqual(profile["source"], "server_props")

    def test_auto_alias_adopts_served_window(self) -> None:
        # The frontend default model is "auto", which the server never echoes
        # back — irrelevant for /props (server-wide n_ctx, no model name).
        cfg = SimpleNamespace(context_window=131072, model="local-model", base_url="http://server/v1")
        with patch("app.infrastructure.llm.openai_compatible.local_llm_config", return_value=cfg), patch(
            "app.infrastructure.llm.openai_compatible.server_context_window",
            return_value=32768,
        ):
            profile = get_active_context_profile("auto")
        self.assertEqual(profile["ctx_size"], 32768)
        self.assertEqual(profile["source"], "server_props")

    def test_legacy_ctx_size_does_not_skip_live_server_lookup(self) -> None:
        cfg = SimpleNamespace(context_window=131072, model="local-model", base_url="http://server/v1")
        with patch("app.infrastructure.llm.openai_compatible.local_llm_config", return_value=cfg), patch(
            "app.infrastructure.llm.openai_compatible.server_context_window",
            return_value=65536,
        ) as props:
            profile = get_active_context_profile("local-model", ctx_size=32768)
        self.assertEqual(profile["ctx_size"], 65536)
        props.assert_called_once_with(fresh=False)


class ContextUsageTest(unittest.TestCase):
    def test_breakdown_and_reserved_output_are_bounded(self) -> None:
        usage = get_context_usage([
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "question"},
            {"role": "tool", "content": "result"},
        ], ctx_size=131072, reserved_output_tokens=4096)
        self.assertEqual(set(usage["breakdown"]), {"system", "chat", "rolling_summary", "tools"})
        self.assertGreater(usage["free_tokens"], 0)
        self.assertLess(usage["percent"], 100)


if __name__ == "__main__":
    unittest.main()
