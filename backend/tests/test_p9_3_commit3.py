"""P9.3 commit 3 — code-agent / workflows / telegram wired to the shared routing.

code-agent has its own loop, so its routing+cap is unit-tested via
_resolve_code_route. workflows + telegram already call run_agent, so we only
assert their default fallback now uses the "auto" sentinel (resolver engages)
while an explicit model still passes through.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import _resolve_code_route  # noqa: E402


_ROUTE_MAP = {
    "code": ["qwen2.5-coder:7b", "qwen3:8b", "gemma3:4b"],
    "chat": ["gemma3:4b"],
}


def _profile(model: str, role: str = "code", *, cloud: bool = False,
             context_limit: int = 16384, timeout: int = 120) -> dict:
    return {
        "id": f"prof-{model}",
        "provider": "anthropic" if cloud else "ollama",
        "model": model,
        "role": role,
        "context_limit": context_limit,
        "timeout_seconds": timeout,
        "enabled": True,
        "cloud_consent_required": cloud,
    }


class CodeAgentRouteResolutionTest(unittest.TestCase):
    def _run(self, model, num_ctx, *, available, profile, monitoring_max):
        models_payload = {
            "ok": available is not None,
            "models": [{"name": m} for m in (available or [])],
        }
        with patch("app.infrastructure.llm.ollama_models.get_models", return_value=models_payload), \
             patch("app.core.config._get_route_map", return_value=_ROUTE_MAP), \
             patch("app.core.config._get_profile_for_role", return_value=profile), \
             patch("app.application.monitoring.runtime.get_agent_limit",
                   return_value=({"max_context_tokens": monitoring_max} if monitoring_max else None)):
            return _resolve_code_route(model, num_ctx)

    def test_auto_selects_code_profile_when_installed(self):
        model, _effective, decision = self._run(
            "auto", 16384, available=["code-pro:7b", "gemma3:4b"],
            profile=_profile("code-pro:7b"), monitoring_max=16384,
        )
        self.assertEqual(model, "code-pro:7b")
        self.assertEqual(decision.source, "profile")
        self.assertEqual(decision.route, "code")
        self.assertEqual(decision.role, "code")

    def test_explicit_model_preserved(self):
        model, _effective, decision = self._run(
            "my-explicit:1b", 16384, available=["my-explicit:1b"],
            profile=_profile("code-pro:7b"), monitoring_max=16384,
        )
        self.assertEqual(model, "my-explicit:1b")
        self.assertEqual(decision.source, "explicit")

    def test_monitoring_cap_applies(self):
        _model, effective, _decision = self._run(
            "qwen2.5-coder:7b", 16384, available=["qwen2.5-coder:7b"],
            profile=None, monitoring_max=4096,
        )
        self.assertEqual(effective, 4096)

    def test_profile_context_limit_applies(self):
        _model, effective, decision = self._run(
            "auto", 16384, available=["code-pro:7b"],
            profile=_profile("code-pro:7b", context_limit=2048), monitoring_max=16384,
        )
        self.assertEqual(decision.source, "profile")
        self.assertEqual(effective, 2048)

    def test_model_safe_ctx_does_not_cap_code_agent(self):
        # qwen2.5-coder:7b has MODEL_SAFE_CTX 6144, but code-agent must NOT apply it.
        _model, effective, _decision = self._run(
            "qwen2.5-coder:7b", 16384, available=["qwen2.5-coder:7b"],
            profile=None, monitoring_max=16384,
        )
        self.assertEqual(effective, 16384)

    def test_default_num_ctx_preserved_without_caps(self):
        _model, effective, _decision = self._run(
            "qwen2.5-coder:7b", 16384, available=["qwen2.5-coder:7b"],
            profile=None, monitoring_max=None,
        )
        self.assertEqual(effective, 16384)

    def test_unavailable_profile_falls_back_to_route_map(self):
        model, _effective, decision = self._run(
            "auto", 16384, available=["gemma3:4b"],  # profile model not installed
            profile=_profile("code-pro:7b"), monitoring_max=16384,
        )
        self.assertEqual(model, "gemma3:4b")
        self.assertEqual(decision.source, "route_map")

    def test_cloud_profile_skipped_without_consent(self):
        model, _effective, decision = self._run(
            "auto", 16384, available=["cloud-x", "qwen2.5-coder:7b"],
            profile=_profile("cloud-x", cloud=True), monitoring_max=16384,
        )
        self.assertEqual(model, "qwen2.5-coder:7b")
        self.assertEqual(decision.source, "route_map")
        self.assertTrue(decision.cloud_skipped)


class WorkflowStepModelFallbackTest(unittest.TestCase):
    def _run_step(self, config, run_context):
        from app.domain.workflows import step_executor

        captured: dict = {}

        def fake_run_agent(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "answer": "x", "meta": {}, "timeline": [], "tool_results": []}

        with patch("app.application.chat.runtime.run_agent", side_effect=fake_run_agent):
            step_executor._execute_agent_step(
                {"id": "s1", "agent_id": "", "config": config},
                {}, run_context, "run-1",
            )
        return captured

    def test_no_model_falls_back_to_auto(self):
        captured = self._run_step({}, {})
        self.assertEqual(captured["model_name"], "auto")

    def test_explicit_step_model_preserved(self):
        captured = self._run_step({"model_name": "qwen3:8b"}, {})
        self.assertEqual(captured["model_name"], "qwen3:8b")

    def test_run_context_model_preserved(self):
        captured = self._run_step({}, {"model_name": "mistral-nemo:latest"})
        self.assertEqual(captured["model_name"], "mistral-nemo:latest")


class TelegramModelFallbackTest(unittest.TestCase):
    def _process(self, config_model):
        from app.application.telegram import runtime as tg

        captured: dict = {}

        def fake_run_agent(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "answer": "hi"}

        cfg = {
            "model": config_model,
            "profile": "Универсальный",
            "use_memory": "false",
            "use_web_search": "false",
        }
        with patch.object(tg, "register_user"), \
             patch.object(tg, "is_user_allowed", return_value=True), \
             patch.object(tg, "log_message"), \
             patch.object(tg, "send_typing"), \
             patch.object(tg, "send_message"), \
             patch.object(tg, "get_config_value", side_effect=lambda key, default="": cfg.get(key, default)), \
             patch("app.application.chat.runtime.run_agent", side_effect=fake_run_agent):
            tg.process_message("token", {"chat": {"id": 1}, "from": {"username": "u"}, "text": "hi"})
        return captured

    def test_empty_model_falls_back_to_auto(self):
        captured = self._process("")
        self.assertEqual(captured["model_name"], "auto")

    def test_explicit_model_preserved(self):
        captured = self._process("qwen3:8b")
        self.assertEqual(captured["model_name"], "qwen3:8b")


if __name__ == "__main__":
    unittest.main()
