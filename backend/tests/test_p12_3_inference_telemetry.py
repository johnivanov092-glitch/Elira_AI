from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import agent_loop  # noqa: E402
from app.application.monitoring.inference import extract_llm_usage  # noqa: E402


class InferenceTelemetryHelperTest(unittest.TestCase):
    def test_extract_llm_usage_from_dict(self) -> None:
        usage = extract_llm_usage({
            "prompt_eval_count": 3,
            "eval_count": 7,
            "total_duration": 1_000_000_000,
            "eval_duration": 500_000_000,
        })

        self.assertEqual(usage["prompt_tokens"], 3)
        self.assertEqual(usage["completion_tokens"], 7)
        self.assertEqual(usage["total_tokens"], 10)
        self.assertEqual(usage["latency_ms"], 1000)
        self.assertAlmostEqual(usage["tokens_per_second"], 14.0)


class CodeAgentInferenceTelemetryTest(unittest.TestCase):
    def test_stream_code_agent_records_inference_telemetry(self) -> None:
        decision = SimpleNamespace(
            route="code",
            provider="llama_server",
            profile_id="prof-code",
            role="code",
            source="profile",
            requested_model="auto",
            fallback_reason=None,
        )

        def fake_chat(**kwargs):
            return {
                "message": {"content": "done", "tool_calls": []},
                "prompt_eval_count": 2,
                "eval_count": 3,
                "total_duration": 100_000_000,
            }

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(agent_loop, "_resolve_code_route", return_value=("test-code", 8192, decision)), \
             patch.object(agent_loop, "_record_code_route_metric"), \
             patch.object(agent_loop, "build_mcp_providers", return_value=[]), \
             patch.object(agent_loop.ToolRegistry, "collect_schemas", return_value=[]), \
             patch("app.application.agent_registry.sandbox.preflight_or_raise",
                   return_value={"limit": {"max_execution_seconds": 600}}), \
             patch.object(agent_loop, "record_inference_telemetry") as telemetry:
            events = list(agent_loop.stream_code_agent(
                user_message="answer",
                project_root=tmp,
                run_id="p12-code-run",
                auto_remember=False,
                chat_fn=fake_chat,
            ))

        self.assertTrue(any(e.get("type") == "done" and e.get("ok") for e in events))
        telemetry.assert_called_once()
        kwargs = telemetry.call_args.kwargs
        self.assertEqual(kwargs["agent_id"], "code-agent")
        self.assertEqual(kwargs["run_id"], "p12-code-run")
        self.assertEqual(kwargs["route"], "code")
        self.assertEqual(kwargs["model"], "test-code")
        self.assertEqual(kwargs["num_ctx"], 8192)
        self.assertEqual(kwargs["completion_chars"], 4)
        self.assertEqual(kwargs["usage"]["total_tokens"], 5)
        self.assertEqual(kwargs["tool_round_trips"], 0)

    def test_stream_code_agent_emits_usage_event(self) -> None:
        decision = SimpleNamespace(
            route="code", provider="llama_server", profile_id="prof-code",
            role="code", source="profile", requested_model="auto", fallback_reason=None,
        )

        def fake_chat(**kwargs):
            return {
                "message": {"content": "done", "tool_calls": []},
                "prompt_eval_count": 2,
                "eval_count": 3,
                "total_duration": 100_000_000,
                "eval_duration": 500_000_000,
            }

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(agent_loop, "_resolve_code_route", return_value=("test-code", 8192, decision)), \
             patch.object(agent_loop, "_record_code_route_metric"), \
             patch.object(agent_loop, "build_mcp_providers", return_value=[]), \
             patch.object(agent_loop.ToolRegistry, "collect_schemas", return_value=[]), \
             patch("app.application.agent_registry.sandbox.preflight_or_raise",
                   return_value={"limit": {"max_execution_seconds": 600}}), \
             patch.object(agent_loop, "record_inference_telemetry"):
            events = list(agent_loop.stream_code_agent(
                user_message="answer", project_root=tmp, run_id="p12-usage-run",
                auto_remember=False, chat_fn=fake_chat,
            ))

        usage_events = [e for e in events if e.get("type") == "usage"]
        self.assertTrue(usage_events, "expected at least one usage event in the stream")
        u = usage_events[0]
        self.assertEqual(u["prompt_tokens"], 2)
        self.assertEqual(u["completion_tokens"], 3)
        self.assertEqual(u["total_tokens"], 5)
        self.assertGreater(u["tokens_per_second"], 0)  # eval_duration set -> 3/0.5s = 6 tok/s


if __name__ == "__main__":
    unittest.main()
