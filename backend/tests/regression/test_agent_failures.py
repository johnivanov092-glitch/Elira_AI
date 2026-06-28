from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
# The endpoint smoke lives at the repo root (scripts/smoke_agent_endpoints.py),
# not under backend/. Put the repo root on sys.path so `scripts.*` resolves.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.application.code_agent.agent_loop import _truncate_for_llm, run_code_agent, stream_code_agent
from app.application.code_agent.inline_tool_calls import _contains_tool_trace, _extract_inline_tool_calls
from app.application.code_agent.tools import tool_recall
from app.application.context.timeouts import timeout_for_task
from app.application.context.usage import get_context_usage
from app.application.tool_providers import ToolRegistry
from app.infrastructure.llm.openai_compatible import _http_error_message, _normalize_messages_for_request, chat_completion
from scripts.smoke_agent_endpoints import Check, _run as run_endpoint_check


class AgentFailureRegressionTest(unittest.TestCase):
    def test_task_timeout_policy_covers_chat_code_and_stress_context(self) -> None:
        self.assertEqual(timeout_for_task("chat"), 120)
        self.assertEqual(timeout_for_task("code"), 600)
        self.assertEqual(timeout_for_task("long_context", ctx_size=262_144), 900)

    def test_unavailable_web_tools_are_not_exposed_or_dispatched(self) -> None:
        registry = ToolRegistry([])
        self.assertNotIn("web_search", registry.known_tools())
        self.assertNotIn("web_fetch", registry.known_tools())
        self.assertIn("unknown tool", registry.dispatch_raw("web_search", {})["text"])

    def test_duplicate_trailing_assistant_messages_are_merged(self) -> None:
        result = _normalize_messages_for_request([
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "part one"},
            {"role": "assistant", "content": "part two"},
            {"role": "invalid", "content": "drop"},
        ])
        self.assertEqual([message["role"] for message in result], ["user", "assistant"])
        self.assertEqual(result[-1]["content"], "part one\n\npart two")

    def test_context_overflow_is_blocked_before_http_send(self) -> None:
        with patch("app.infrastructure.llm.openai_compatible.local_llm_config") as config, patch(
            "app.infrastructure.llm.openai_compatible.requests.post"
        ) as post:
            config.return_value.enabled = True
            config.return_value.model = "local-model"
            config.return_value.provider = "llama_server"
            config.return_value.base_url = "http://server/v1"
            config.return_value.api_key = "local"
            config.return_value.timeout_seconds = 120
            config.return_value.max_tokens = 128
            config.return_value.context_window = 1024
            with self.assertRaisesRegex(RuntimeError, "blocked before send"):
                chat_completion(model="local-model", messages=[{"role": "user", "content": "X" * 5000}], options={"num_ctx": 1024})
            post.assert_not_called()

    def test_agent_reduces_critical_recent_history_before_model_request(self) -> None:
        captured: dict[str, object] = {}

        def bounded_chat(**kwargs):
            if kwargs.get("tools"):
                captured["messages"] = kwargs["messages"]
                return {"message": {"content": "done", "tool_calls": []}}
            return {"message": {"content": "summary", "tool_calls": []}}

        history = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": str(index) + "X" * 12_000}
            for index in range(8)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = run_code_agent(
                user_message="finish safely",
                project_root=tmp,
                model="test-model",
                num_ctx=32_768,
                conversation_history=history,
                chat_fn=bounded_chat,
            )

        self.assertTrue(result["ok"], result.get("error"))
        usage = get_context_usage(
            captured["messages"],  # type: ignore[arg-type]
            ctx_size=32_768,
            reserved_output_tokens=4096,
            reserved_system_tokens=4096,
            safety_margin_tokens=2048,
        )
        self.assertLessEqual(usage["current_tokens"], 22_528)
        self.assertLess(usage["percent"], 95.0)

    def test_404_error_names_service_endpoint_and_path(self) -> None:
        response = Mock(status_code=404, text="File Not Found", reason="Not Found")
        exc = __import__("requests").HTTPError(response=response)
        message = _http_error_message(exc, service="main LLM", endpoint="http://server/v1", path="chat/completions")
        self.assertIn("main LLM", message)
        self.assertIn("http://server/v1/chat/completions", message)
        self.assertIn("Verify", message)

    def test_xml_tool_trace_is_recovered_only_for_available_tool(self) -> None:
        raw = '<tool_call><function=glob>{"pattern":"*.py"}</function></tool_call>'
        self.assertTrue(_contains_tool_trace(raw))
        self.assertEqual(_extract_inline_tool_calls(raw, {"glob"})[0]["function"]["name"], "glob")
        self.assertEqual(_extract_inline_tool_calls(raw, set()), [])

    def test_repeated_identical_tool_call_trips_loop_guard(self) -> None:
        def looping_chat(**kwargs):
            if not kwargs.get("tools"):
                return {"message": {"content": "controlled final", "tool_calls": []}}
            return {"message": {"content": "", "tool_calls": [{"function": {"name": "glob", "arguments": {"pattern": "*"}}}]}}

        with tempfile.TemporaryDirectory() as tmp:
            result = run_code_agent(user_message="inspect", project_root=tmp, model="test-model", max_steps=10, chat_fn=looping_chat)
        self.assertEqual(result["stop_reason"], "loop_guard")
        self.assertTrue(result["response"])

    def test_repeated_todo_update_is_exempt_from_loop_guard(self) -> None:
        # Local models routinely re-emit the full checklist verbatim. The loop
        # fingerprint is taken before run_id is injected, so identical todo_update
        # calls share a fingerprint and the repeat counter climbs past the limit.
        # todo_update is idempotent (upserted by position), so a benign repeat
        # must NOT kill the run — it should run out via max_steps instead.
        def todo_loop(**kwargs):
            if not kwargs.get("tools"):
                return {"message": {"content": "controlled final", "tool_calls": []}}
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "todo_update",
                            "arguments": {
                                "items": [{"text": "build index.html", "status": "pending", "position": 1}]
                            },
                        }
                    }],
                }
            }

        def fake_todo_update(*, run_id, items=None, updates=None):
            return {
                "ok": True,
                "run_id": run_id,
                "items": [
                    {"id": "it-1", "text": "build index.html", "status": "pending", "position": 1}
                ],
                "changed": [],
                "count": 1,
            }

        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.application.task_planner.service.todo_update", side_effect=fake_todo_update
        ):
            # max_steps comfortably exceeds the repeat limit (4) so, absent the
            # exemption, the guard would fire well before max_steps is reached.
            result = run_code_agent(
                user_message="plan",
                project_root=tmp,
                model="test-model",
                max_steps=8,
                chat_fn=todo_loop,
            )

        self.assertNotEqual(result["stop_reason"], "loop_guard", result)
        self.assertEqual(result["stop_reason"], "max_steps")
        self.assertTrue(result["ok"])

    def test_max_steps_wrap_up_is_a_controlled_partial_result(self) -> None:
        calls = 0

        def bounded_chat(**kwargs):
            nonlocal calls
            if not kwargs.get("tools"):
                return {
                    "message": {
                        "content": "PARTIAL RESULT\nСделано: анализ.\nНе сделано: правка.",
                        "tool_calls": [],
                    }
                }
            calls += 1
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "glob",
                            "arguments": {"pattern": f"step-{calls}-*"},
                        }
                    }],
                }
            }

        with tempfile.TemporaryDirectory() as tmp:
            result = run_code_agent(
                user_message="inspect",
                project_root=tmp,
                model="test-model",
                max_steps=2,
                chat_fn=bounded_chat,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["stop_reason"], "max_steps")
        self.assertIsNone(result["error"])
        self.assertTrue(result["partial"])
        self.assertIn("PARTIAL RESULT", result["response"])

    def test_long_llm_call_emits_heartbeat(self) -> None:
        def slow_chat(**kwargs):
            time.sleep(0.04)
            return {"message": {"content": "done", "tool_calls": []}}

        with tempfile.TemporaryDirectory() as tmp, patch("app.application.code_agent.agent_loop._LLM_HEARTBEAT_EVERY", 0.01):
            events = list(stream_code_agent(user_message="work", project_root=tmp, model="test-model", chat_fn=slow_chat))
        self.assertTrue(any(event.get("type") == "heartbeat" for event in events))
        self.assertEqual(events[-1]["stop_reason"], "answer")

    def test_streaming_agent_emits_visible_deltas_and_final_response(self) -> None:
        def stream_chat(**kwargs):
            yield {"type": "delta", "content": "Hello "}
            yield {"type": "delta", "content": "world"}
            yield {"type": "message", "response": {
                "message": {"content": "Hello world", "tool_calls": []},
                "prompt_eval_count": 4,
                "eval_count": 2,
                "total_duration": 1_000_000_000,
            }}

        with tempfile.TemporaryDirectory() as tmp:
            events = list(stream_code_agent(
                user_message="hello",
                project_root=tmp,
                model="test-model",
                chat_fn=lambda **_: {},
                chat_stream_fn=stream_chat,
            ))
        self.assertEqual("".join(str(event.get("text") or "") for event in events if event.get("type") == "delta"), "Hello world")
        self.assertEqual(next(event["text"] for event in events if event.get("type") == "final_response"), "Hello world")

    def test_streaming_xml_trace_is_not_emitted_as_delta(self) -> None:
        calls = 0

        def stream_chat(**kwargs):
            nonlocal calls
            calls += 1
            content = '<tool_call><function=missing>{}</function></tool_call>' if calls == 1 else "safe answer"
            yield {"type": "delta", "content": content}
            yield {"type": "message", "response": {"message": {"content": content, "tool_calls": []}}}

        with tempfile.TemporaryDirectory() as tmp:
            events = list(stream_code_agent(
                user_message="hello",
                project_root=tmp,
                model="test-model",
                chat_fn=lambda **_: {},
                chat_stream_fn=stream_chat,
                max_steps=3,
            ))
        visible = "".join(str(event.get("text") or "") for event in events if event.get("type") == "delta")
        self.assertNotIn("tool_call", visible)
        self.assertIn("safe answer", visible)

    def test_streaming_fragmented_xml_trace_is_not_emitted_as_delta(self) -> None:
        calls = 0

        def stream_chat(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                parts = ["A" * 90 + "<tool_", "call><function=missing>", "{}</function></tool_call>"]
                content = "".join(parts)
            else:
                parts = ["safe answer"]
                content = parts[0]
            for part in parts:
                yield {"type": "delta", "content": part}
            yield {"type": "message", "response": {"message": {"content": content, "tool_calls": []}}}

        with tempfile.TemporaryDirectory() as tmp:
            events = list(stream_code_agent(
                user_message="hello", project_root=tmp, model="test-model",
                chat_fn=lambda **_: {}, chat_stream_fn=stream_chat, max_steps=3,
            ))
        visible = "".join(str(event.get("text") or "") for event in events if event.get("type") == "delta")
        self.assertNotIn("tool_call", visible)
        self.assertIn("safe answer", visible)

    def test_slow_stream_emits_heartbeat(self) -> None:
        def stream_chat(**kwargs):
            time.sleep(0.04)
            yield {"type": "delta", "content": "done"}
            yield {"type": "message", "response": {"message": {"content": "done", "tool_calls": []}}}

        with tempfile.TemporaryDirectory() as tmp, patch("app.application.code_agent.agent_loop._LLM_HEARTBEAT_EVERY", 0.01):
            events = list(stream_code_agent(
                user_message="work", project_root=tmp, model="test-model",
                chat_fn=lambda **_: {}, chat_stream_fn=stream_chat,
            ))
        self.assertTrue(any(event.get("type") == "heartbeat" for event in events))

    def test_live_server_context_caps_code_agent_request(self) -> None:
        captured: dict[str, int] = {}

        def fake_stream(**kwargs):
            captured["num_ctx"] = int(kwargs["options"]["num_ctx"])
            captured["active_context_limit"] = int(kwargs["options"]["active_context_limit"])
            yield {
                "type": "message",
                "response": {"message": {"content": "done", "tool_calls": []}},
            }

        profile = {
            "active_model": "local-model",
            "model_alias": "local-model",
            "main_endpoint": "http://server/v1",
            "ctx_size": 32_768,
            "reserved_output_tokens": 4096,
            "reserved_system_tokens": 4096,
            "safety_margin_tokens": 2048,
            "safe_input_budget": 22_528,
            "mode": "32k",
            "source": "server",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.application.context.profile.get_active_context_profile",
            return_value=profile,
        ), patch(
            "app.application.code_agent.agent_loop._local_chat_stream",
            side_effect=fake_stream,
        ):
            events = list(stream_code_agent(
                user_message="answer",
                project_root=tmp,
                model="auto",
                num_ctx=131_072,
                max_steps=1,
            ))

        self.assertEqual(captured["num_ctx"], 32_768)
        self.assertEqual(captured["active_context_limit"], 32_768)
        self.assertEqual(events[-1]["stop_reason"], "answer")

    def test_large_tool_result_keeps_head_and_tail(self) -> None:
        value = "HEAD" + "X" * 30_000 + "TAIL"
        result = _truncate_for_llm(value, 1000)
        self.assertLess(len(result), 1200)
        self.assertTrue(result.startswith("HEAD"))
        self.assertTrue(result.endswith("TAIL"))

    def test_unavailable_rag_returns_controlled_fallback(self) -> None:
        with patch("app.application.rag_memory.service.search_rag", return_value={"ok": False, "error": "embedding unavailable"}):
            result = tool_recall(Path.cwd(), query="x")
        self.assertIn("ERROR: embedding unavailable", result["text"])

    def test_unavailable_ocr_or_vision_is_reported_without_crash(self) -> None:
        with patch("scripts.smoke_agent_endpoints.requests.request", side_effect=__import__("requests").ConnectionError("offline")):
            for service in ("ocr-health", "vision-health"):
                result = run_endpoint_check(Check(service, "GET", f"http://server/{service}", 1))
                self.assertFalse(result["ok"])
                self.assertEqual(result["service"], service)
                self.assertIn("offline", result["error"])


if __name__ == "__main__":
    unittest.main()
