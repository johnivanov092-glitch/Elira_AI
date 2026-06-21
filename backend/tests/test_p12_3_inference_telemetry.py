from __future__ import annotations

import json
import sqlite3
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

from app.application.chat import runtime as agents_service  # noqa: E402
from app.application.code_agent import agent_loop  # noqa: E402
from app.application.monitoring import runtime as agent_monitor  # noqa: E402
from app.application.monitoring.inference import extract_llm_usage  # noqa: E402
from test_agent_os_phase5 import AgentOsPhase5DbMixin  # noqa: E402


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


class ChatInferenceTelemetryTest(AgentOsPhase5DbMixin):
    def test_run_agent_records_model_inference_metric_and_resources(self) -> None:
        usage = {
            "prompt_tokens": 5,
            "completion_tokens": 7,
            "total_tokens": 12,
            "latency_ms": 123,
            "tokens_per_second": 56.0,
        }
        with patch.object(agents_service.PlannerV2Service, "plan", return_value=self._base_plan()), \
             patch.object(agents_service, "_collect_context", return_value=""), \
             patch.object(
                 agents_service,
                 "run_chat",
                 return_value={"ok": True, "answer": "hello from agent", "meta": {"usage": usage}},
             ), \
             patch.object(agents_service, "observe_dialogue", return_value={"ok": True}), \
             patch.object(agents_service, "_get_and_clear_attachments", return_value=""), \
             patch.object(agents_service, "_maybe_generate_files", return_value=""), \
             patch.object(
                 agents_service,
                 "_maybe_auto_exec_python",
                 side_effect=lambda user_input, answer, timeline, enabled=True: answer,
             ), \
             patch.object(agents_service, "_chat_available_models", return_value=None):
            result = agents_service.run_agent(
                model_name="test-model",
                profile_name="Universal",
                user_input="Hello",
                session_id="p12-3-session",
                use_memory=False,
                use_library=False,
                use_web_search=False,
                num_ctx=1000,
            )

        self.assertTrue(result["ok"])
        con = sqlite3.connect(str(agent_monitor.DB_PATH))
        try:
            row = con.execute(
                "SELECT details_json FROM agent_metrics WHERE metric_type = 'model.inference' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            resources = dict(con.execute(
                "SELECT resource, amount FROM resource_usage WHERE resource LIKE 'llm_%'"
            ).fetchall())
        finally:
            con.close()

        self.assertIsNotNone(row)
        details = json.loads(row[0])
        self.assertEqual(details["provider"], "")
        self.assertEqual(details["model"], "test-model")
        self.assertEqual(details["prompt_tokens"], 5)
        self.assertEqual(details["completion_tokens"], 7)
        self.assertEqual(details["total_tokens"], 12)
        self.assertEqual(details["tool_round_trips"], 0)
        self.assertTrue(details["usage_available"])
        self.assertEqual(resources["llm_total_tokens"], 12)
        self.assertEqual(resources["llm_latency"], 123)


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
             patch.object(agent_loop, "_resolve_code_route", return_value=("test-code", 4096, decision)), \
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
        self.assertEqual(kwargs["num_ctx"], 4096)
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
             patch.object(agent_loop, "_resolve_code_route", return_value=("test-code", 4096, decision)), \
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
