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
from app.application.monitoring.inference import (  # noqa: E402
    extract_llm_usage,
    record_inference_telemetry,
)


class InferenceTelemetryHelperTest(unittest.TestCase):
    def test_extract_llm_usage_from_dict(self) -> None:
        usage = extract_llm_usage({
            "prompt_eval_count": 100,
            "cached_prompt_tokens": 80,
            "prompt_cache_hit_ratio": 0.8,
            "eval_count": 20,
            "total_duration": 1_000_000_000,
            "prompt_eval_duration": 50_000_000,
            "eval_duration": 500_000_000,
            "server_prompt_tokens_per_second": 400.0,
            "server_tokens_per_second": 40.0,
            "ttft_ms": 125,
        })

        self.assertEqual(usage["prompt_tokens"], 100)
        self.assertEqual(usage["cached_prompt_tokens"], 80)
        self.assertAlmostEqual(usage["prompt_cache_hit_ratio"], 0.8)
        self.assertEqual(usage["completion_tokens"], 20)
        self.assertEqual(usage["total_tokens"], 120)
        self.assertEqual(usage["latency_ms"], 1000)
        self.assertEqual(usage["prompt_duration_ms"], 50)
        self.assertEqual(usage["completion_duration_ms"], 500)
        self.assertEqual(usage["prompt_tokens_per_second"], 400.0)
        self.assertEqual(usage["tokens_per_second"], 40.0)
        self.assertEqual(usage["ttft_ms"], 125)

    def test_recorded_metric_keeps_cache_throughput_and_model_ttft(self) -> None:
        with patch("app.application.monitoring.runtime.record_metric") as metric, \
             patch("app.application.monitoring.runtime.record_resource_usage"):
            record_inference_telemetry(
                agent_id="code-agent",
                run_id="metrics-run",
                route="code",
                model="local-model",
                ok=True,
                usage={
                    "prompt_tokens": 100,
                    "cached_prompt_tokens": 80,
                    "prompt_cache_hit_ratio": 0.8,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "prompt_tokens_per_second": 400.0,
                    "tokens_per_second": 40.0,
                    "ttft_ms": 125,
                },
            )

        details = metric.call_args.kwargs["details"]
        self.assertEqual(details["cached_prompt_tokens"], 80)
        self.assertEqual(details["prompt_cache_hit_ratio"], 0.8)
        self.assertEqual(details["prompt_tokens_per_second"], 400.0)
        self.assertEqual(details["tokens_per_second"], 40.0)
        self.assertEqual(details["ttft_ms"], 125)


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
             patch.object(agent_loop, "_resolve_code_route", return_value=("test-code", 131072, decision)), \
             patch.object(agent_loop, "_record_code_route_metric"), \
             patch.object(agent_loop, "build_runtime_tool_registry", return_value=agent_loop.ToolRegistry([])), \
             patch.object(agent_loop.ToolRegistry, "collect_schemas", return_value=[]), \
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
        self.assertEqual(kwargs["num_ctx"], 131072)
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
                "prompt_eval_count": 10,
                "cached_prompt_tokens": 8,
                "prompt_cache_hit_ratio": 0.8,
                "eval_count": 5,
                "total_duration": 100_000_000,
                "eval_duration": 500_000_000,
                "server_prompt_tokens_per_second": 250.0,
                "server_tokens_per_second": 50.0,
                "ttft_ms": 40,
            }

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(agent_loop, "_resolve_code_route", return_value=("test-code", 131072, decision)), \
             patch.object(agent_loop, "_record_code_route_metric"), \
             patch.object(agent_loop, "build_runtime_tool_registry", return_value=agent_loop.ToolRegistry([])), \
             patch.object(agent_loop.ToolRegistry, "collect_schemas", return_value=[]), \
             patch.object(agent_loop, "record_inference_telemetry"):
            events = list(agent_loop.stream_code_agent(
                user_message="answer", project_root=tmp, run_id="p12-usage-run",
                auto_remember=False, chat_fn=fake_chat,
            ))

        usage_events = [e for e in events if e.get("type") == "usage"]
        self.assertTrue(usage_events, "expected at least one usage event in the stream")
        u = usage_events[0]
        self.assertEqual(u["prompt_tokens"], 10)
        self.assertEqual(u["cached_prompt_tokens"], 8)
        self.assertAlmostEqual(u["cache_hit_ratio"], 0.8)
        self.assertEqual(u["completion_tokens"], 5)
        self.assertEqual(u["total_tokens"], 15)
        self.assertEqual(u["prompt_tokens_per_second"], 250.0)
        self.assertEqual(u["tokens_per_second"], 50.0)
        self.assertEqual(u["ttft_ms"], 40)


if __name__ == "__main__":
    unittest.main()
