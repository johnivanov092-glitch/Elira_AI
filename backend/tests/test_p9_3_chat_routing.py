"""P9.3 commit 2 — model profiles activated in the chat execution path.

Exercises prepare_chat_execution end-to-end with the REAL resolver +
effective_context_limit, stubbing only the DB lookups (route map / profile),
the available-models probe, the monitoring caps, preflight and metrics.
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

from app.core.config import (  # noqa: E402
    effective_context_limit,
    resolve_model_for_route,
)
from app.application.chat.service import prepare_chat_execution  # noqa: E402


_ROUTE_MAP = {
    "chat":     ["chat-model", "backup-model"],
    "research": ["research-model", "backup-model"],
    "code":     ["code-model", "chat-model"],
}


def _profile(model: str, role: str = "fast", *, cloud: bool = False,
             context_limit: int = 16384, timeout: int = 30) -> dict:
    return {
        "id": f"prof-{model}",
        "provider": "anthropic" if cloud else "llama_server",
        "model": model,
        "role": role,
        "context_limit": context_limit,
        "timeout_seconds": timeout,
        "enabled": True,
        "cloud_consent_required": cloud,
    }


class _History:
    def add_event(self, *args, **kwargs):
        return None


class ChatRoutingActivationTest(unittest.TestCase):
    def setUp(self):
        self.metrics: list[dict] = []
        self.preflight_calls: list[dict] = []

    def _run(self, *, model_name="auto", route="chat", available_models=None,
             num_ctx=8192, monitoring_max=None, profile=None):
        def _record_metric(**kwargs):
            self.metrics.append(kwargs)
            return {}

        def _preflight(**kwargs):
            self.preflight_calls.append(kwargs)
            return {"ok": True}

        with patch("app.core.config._get_route_map", return_value=_ROUTE_MAP), \
             patch("app.core.config._get_profile_for_role", return_value=profile):
            return prepare_chat_execution(
                planner_input="hello",
                model_name=model_name,
                plan_runner=lambda _: {"route": route, "tools": []},
                use_memory=False,
                use_library=False,
                use_web_search=False,
                is_memory_command_func=lambda _: False,
                resolve_model_for_route_func=resolve_model_for_route,
                effective_context_limit_func=effective_context_limit,
                available_models_func=lambda: available_models,
                get_max_context_tokens_func=lambda _aid: monitoring_max,
                record_metric_func=_record_metric,
                history_service=_History(),
                run_id="run-1",
                extract_and_save_func=lambda _: [],
                preflight_or_raise_func=_preflight,
                agent_id="agent-1",
                num_ctx=num_ctx,
                streaming=False,
            )

    def test_auto_uses_enabled_local_profile_when_installed(self):
        execution = self._run(
            model_name="auto", route="chat",
            profile=_profile("profile-model", role="fast"),
            available_models=["chat-model", "profile-model"],
        )
        self.assertEqual(execution.effective_model, "profile-model")
        self.assertEqual(execution.decision.source, "profile")

    def test_unavailable_profile_falls_back_to_route_map(self):
        execution = self._run(
            model_name="auto", route="chat",
            profile=_profile("profile-model", role="fast"),
            available_models=["chat-model"],  # profile model not installed
        )
        self.assertEqual(execution.effective_model, "chat-model")
        self.assertEqual(execution.decision.source, "route_map")
        self.assertEqual(execution.decision.fallback_reason, "profile_model_unavailable")

    def test_cloud_profile_skipped_without_consent(self):
        execution = self._run(
            model_name="auto", route="research",
            profile=_profile("claude-sonnet-4-5", role="strong", cloud=True),
            available_models=["claude-sonnet-4-5", "research-model"],
        )
        self.assertEqual(execution.effective_model, "research-model")
        self.assertEqual(execution.decision.source, "route_map")
        self.assertTrue(execution.decision.cloud_skipped)

    def test_explicit_model_kept_but_context_capped(self):
        execution = self._run(
            model_name="my-explicit-model", route="chat",
            available_models=["my-explicit-model"],
            num_ctx=99999, monitoring_max=8192,
            profile=_profile("profile-model"),  # ignored: explicit wins
        )
        self.assertEqual(execution.effective_model, "my-explicit-model")
        self.assertEqual(execution.decision.source, "explicit")
        # explicit choice does NOT bypass the monitoring context cap
        self.assertEqual(execution.effective_num_ctx, 8192)

    def test_sandbox_preflight_receives_effective_num_ctx(self):
        execution = self._run(
            model_name="auto", route="chat",
            available_models=["chat-model"],
            num_ctx=99999, monitoring_max=4096,
            profile=None,
        )
        self.assertEqual(execution.effective_num_ctx, 4096)
        self.assertEqual(len(self.preflight_calls), 1)
        self.assertEqual(self.preflight_calls[0]["num_ctx"], 4096)

    def test_known_model_capped_by_model_safe_ctx(self):
        execution = self._run(
            model_name="local-model", route="code",
            available_models=["local-model"],
            num_ctx=32768, monitoring_max=None, profile=None,
        )
        self.assertEqual(execution.effective_model, "local-model")
        self.assertEqual(execution.effective_num_ctx, 32768)

    def test_unknown_model_not_cut_to_default(self):
        # unknown model + no monitoring cap -> num_ctx unchanged (no auto-cut).
        execution = self._run(
            model_name="some-unknown-model:1b", route="chat",
            available_models=["some-unknown-model:1b"],
            num_ctx=8192, monitoring_max=None, profile=None,
        )
        self.assertEqual(execution.effective_num_ctx, 8192)

    def test_local_models_unavailable_keeps_profile_inert(self):
        # available_models None -> profile inert -> route_map.
        execution = self._run(
            model_name="auto", route="chat",
            profile=_profile("profile-model", role="fast"),
            available_models=None,
        )
        self.assertEqual(execution.effective_model, "chat-model")
        self.assertEqual(execution.decision.source, "route_map")
        self.assertEqual(execution.decision.fallback_reason, "available_models_unknown")

    def test_profile_timeout_surfaced(self):
        execution = self._run(
            model_name="auto", route="chat",
            profile=_profile("profile-model", role="fast", timeout=45),
            available_models=["profile-model"],
        )
        self.assertEqual(execution.effective_timeout_seconds, 45)

    def test_routing_metric_records_provenance(self):
        self._run(
            model_name="auto", route="chat",
            profile=_profile("profile-model", role="fast"),
            available_models=["profile-model"],
        )
        routed = [m for m in self.metrics if m.get("metric_type") == "model.routed"]
        self.assertEqual(len(routed), 1)
        details = routed[0]["details"]
        self.assertEqual(details["model"], "profile-model")
        self.assertEqual(details["provider"], "llama_server")
        self.assertEqual(details["profile_id"], "prof-profile-model")
        self.assertEqual(details["routing_source"], "profile")
        self.assertEqual(details["route"], "chat")
        self.assertEqual(details["role"], "fast")
        self.assertIn("effective_num_ctx", details)
        self.assertIn("requested_model", details)
        self.assertIn("fallback_reason", details)


if __name__ == "__main__":
    unittest.main()
