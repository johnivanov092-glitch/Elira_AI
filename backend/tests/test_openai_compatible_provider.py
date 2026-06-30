from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent import history as agent_history  # noqa: E402
from app.infrastructure.llm import local_models, openai_compatible  # noqa: E402


def _llama_env() -> dict[str, str]:
    return {
        "LLAMA_SERVER_ENABLED": "true",
        "LLAMA_SERVER_BASE_URL": "http://ai-server:8000/v1",
        "LLAMA_SERVER_MODEL": "local-model",
        "LLAMA_SERVER_API_KEY": "local-key",
        "LLAMA_SERVER_TIMEOUT_SECONDS": "12",
    }


class _Response:
    def __init__(self, payload: dict, lines: list[str | bytes] | None = None) -> None:
        self._payload = payload
        self._lines = lines or []
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload

    def iter_lines(self, decode_unicode: bool = False):
        yield from self._lines

    def close(self) -> None:
        self.closed = True


class OpenAICompatibleProviderTest(unittest.TestCase):
    def test_request_messages_drop_invalid_empty_and_merge_trailing_assistants(self) -> None:
        normalized = openai_compatible._normalize_messages_for_request([
            {"role": "invalid", "content": "drop"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "part one"},
            {"role": "assistant", "content": "part two"},
        ])
        self.assertEqual(normalized, [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "part one\n\npart two"},
        ])

    def test_disabled_provider_is_inert(self) -> None:
        with patch.dict(os.environ, {"LLAMA_SERVER_ENABLED": "false"}, clear=False):
            self.assertFalse(openai_compatible.is_local_llm_model("local-model"))
            self.assertEqual(openai_compatible.list_models(), [])

    def test_chat_completion_returns_local_llm_payload(self) -> None:
        response = _Response(
            {
                "model": "local-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "OK",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path": "README.md"}',
                                    }
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }
        )
        with patch.dict(os.environ, _llama_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
            return_value=response,
        ) as post:
            result = openai_compatible.chat_completion(
                model="local-model",
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "read_file"}}],
                options={"temperature": 0},
            )

        self.assertEqual(result["message"]["content"], "OK")
        self.assertEqual(result["message"]["tool_calls"][0]["function"]["arguments"], {"path": "README.md"})
        self.assertEqual(result["prompt_eval_count"], 5)
        self.assertEqual(result["eval_count"], 2)
        self.assertEqual(result["provider"], "llama_server")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "local-model")
        self.assertEqual(post.call_args.kwargs["json"]["temperature"], 0)
        self.assertIn("tools", post.call_args.kwargs["json"])

    def test_chat_completion_retries_transient_then_succeeds(self) -> None:
        good = _Response({"model": "local-model", "choices": [{"message": {"role": "assistant", "content": "OK"}}]})
        attempts = [
            openai_compatible.requests.ConnectionError("connection dropped"),
            openai_compatible.requests.Timeout("read timed out"),
            good,
        ]
        with patch.dict(os.environ, _llama_env(), clear=False), \
             patch("app.infrastructure.llm.openai_compatible.time.sleep") as sleep, \
             patch("app.infrastructure.llm.openai_compatible.requests.post", side_effect=attempts) as post:
            result = openai_compatible.chat_completion(
                model="local-model", messages=[{"role": "user", "content": "hi"}]
            )
        self.assertEqual(result["message"]["content"], "OK")
        self.assertEqual(post.call_count, 3)   # 2 transient failures + 1 success
        self.assertEqual(sleep.call_count, 2)  # backoff before each retry

    def test_chat_completion_does_not_retry_client_error(self) -> None:
        err = openai_compatible.requests.HTTPError("400 Bad Request")
        err.response = SimpleNamespace(status_code=400, text="Bad Request")
        bad = _Response({})
        bad.raise_for_status = MagicMock(side_effect=err)
        with patch.dict(os.environ, _llama_env(), clear=False), \
             patch("app.infrastructure.llm.openai_compatible.time.sleep") as sleep, \
             patch("app.infrastructure.llm.openai_compatible.requests.post", return_value=bad) as post:
            with self.assertRaises(RuntimeError):
                openai_compatible.chat_completion(
                    model="local-model", messages=[{"role": "user", "content": "hi"}]
                )
        post.assert_called_once()   # 4xx is not retried
        sleep.assert_not_called()

    def test_configured_server_context_caps_larger_request_option(self) -> None:
        env = {**_llama_env(), "LLAMA_SERVER_CONTEXT_WINDOW": "32768"}
        with patch.dict(os.environ, env, clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
        ) as post:
            with self.assertRaisesRegex(RuntimeError, "blocked before send"):
                openai_compatible.chat_completion(
                    model="local-model",
                    messages=[{"role": "user", "content": "X" * 140_000}],
                    options={"num_ctx": 131_072},
                )
        post.assert_not_called()

    def test_active_context_limit_overrides_stale_lower_config(self) -> None:
        env = {**_llama_env(), "LLAMA_SERVER_CONTEXT_WINDOW": "32768"}
        response = _Response(
            {
                "model": "local-model",
                "choices": [{"message": {"role": "assistant", "content": "OK"}}],
            }
        )
        with patch.dict(os.environ, env, clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
            return_value=response,
        ) as post:
            result = openai_compatible.chat_completion(
                model="local-model",
                messages=[{"role": "user", "content": "X" * 140_000}],
                options={"num_ctx": 131_072, "active_context_limit": 131_072},
            )

        self.assertEqual(result["message"]["content"], "OK")
        post.assert_called_once()

    def test_chat_completion_serializes_tool_history_for_openai_api(self) -> None:
        response = _Response(
            {
                "model": "local-model",
                "choices": [{"message": {"role": "assistant", "content": "done", "tool_calls": []}}],
            }
        )
        with patch.dict(os.environ, _llama_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
            return_value=response,
        ) as post:
            openai_compatible.chat_completion(
                model="local-model",
                messages=[
                    {"role": "user", "content": "run it"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "run_bash", "arguments": {"command": "echo ok"}}}
                        ],
                    },
                    {"role": "tool", "name": "run_bash", "content": "ok"},
                ],
                tools=[{"type": "function", "function": {"name": "run_bash"}}],
            )

        payload_messages = post.call_args.kwargs["json"]["messages"]
        tool_call = payload_messages[1]["tool_calls"][0]
        self.assertEqual(tool_call["type"], "function")
        self.assertTrue(tool_call["id"])
        self.assertEqual(tool_call["function"]["name"], "run_bash")
        self.assertEqual(tool_call["function"]["arguments"], '{"command":"echo ok"}')
        self.assertEqual(payload_messages[2], {
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": "ok",
        })

    def test_stream_yields_tokens_and_closes_response(self) -> None:
        response = _Response(
            {},
            lines=[
                'data: {"choices": [{"delta": {"content": "O"}}]}',
                'data: {"choices": [{"delta": {"content": "K"}}]}',
                "data: [DONE]",
            ],
        )
        with patch.dict(os.environ, _llama_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
            return_value=response,
        ):
            tokens = list(
                openai_compatible.chat_completion_stream(
                    model="local-model",
                    messages=[{"role": "user", "content": "hi"}],
                )
            )

        self.assertEqual(tokens, ["O", "K"])
        self.assertTrue(response.closed)

    def test_stream_decodes_utf8_sse_bytes(self) -> None:
        response = _Response(
            {},
            lines=[
                'data: {"choices": [{"delta": {"content": "Привет"}}]}'.encode("utf-8"),
                b"data: [DONE]",
            ],
        )
        with patch.dict(os.environ, _llama_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
            return_value=response,
        ):
            tokens = list(
                openai_compatible.chat_completion_stream(
                    model="local-model",
                    messages=[{"role": "user", "content": "hi"}],
                )
            )

        self.assertEqual(tokens, ["Привет"])
        self.assertTrue(response.closed)

    def test_event_stream_assembles_fragmented_tool_call(self) -> None:
        response = _Response(
            {},
            lines=[
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_","arguments":"{\\"path\\":"}}]}}]}',
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"file","arguments":"\\"README.md\\"}"}}]}}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":4}}',
                "data: [DONE]",
            ],
        )
        with patch.dict(os.environ, _llama_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
            return_value=response,
        ) as post:
            events = list(openai_compatible.chat_completion_event_stream(
                model="local-model",
                messages=[{"role": "user", "content": "read"}],
                tools=[{"type": "function", "function": {"name": "read_file"}}],
                options={"num_ctx": 131_072},
            ))

        result = events[-1]["response"]
        call = result["message"]["tool_calls"][0]
        self.assertEqual(call["id"], "call_1")
        self.assertEqual(call["function"]["name"], "read_file")
        self.assertEqual(call["function"]["arguments"], '{"path":"README.md"}')
        self.assertEqual(result["prompt_eval_count"], 12)
        self.assertEqual(result["eval_count"], 4)
        self.assertTrue(post.call_args.kwargs["json"]["stream"])
        self.assertIn("tools", post.call_args.kwargs["json"])
        self.assertTrue(response.closed)

    def test_model_list_uses_openai_models_endpoint(self) -> None:
        response = _Response({"data": [{"id": "local-model", "root": "Qwen/Qwen2.5-7B-Instruct", "n_ctx": 16384}]})
        with patch.dict(os.environ, _llama_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.get",
            return_value=response,
        ):
            models = openai_compatible.list_models()

        self.assertEqual(models[0]["name"], "local-model")
        self.assertEqual(models[0]["provider"], "llama_server")
        self.assertEqual(models[0]["context_window"], 16384)

    def test_model_list_reads_llama_cpp_meta_context(self) -> None:
        response = _Response({"data": [{"id": "local-model", "meta": {"n_ctx": 16384}}]})
        with patch.dict(os.environ, _llama_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.get",
            return_value=response,
        ):
            models = openai_compatible.list_models()

        self.assertEqual(models[0]["context_window"], 16384)

    def test_local_model_listing_uses_openai_compatible_provider(self) -> None:
        with patch.object(
            local_models,
            "list_openai_compatible_models",
            return_value=[{"name": "local-model", "model": "local-model", "provider": "llama_server"}],
        ):
            result = local_models.get_models()

        self.assertTrue(result["ok"])
        self.assertEqual(result["models"][0]["name"], "local-model")

    def test_code_agent_chat_wrapper_routes_local_model_to_llama_server(self) -> None:
        # _local_chat was moved to code_agent.history (re-exported on agent_loop);
        # it resolves `chat_completion` from its own module, so patch it there.
        with patch.dict(os.environ, _llama_env(), clear=False), patch.object(
            agent_history,
            "chat_completion",
            return_value={"message": {"content": "OK", "tool_calls": []}},
        ) as chat_completion:
            result = agent_loop._local_chat(
                model="local-model",
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "read_file"}}],
                options={"num_ctx": 8192},
            )

        self.assertEqual(result["message"]["content"], "OK")
        chat_completion.assert_called_once()


class LocalEmbedProviderTest(unittest.TestCase):
    @staticmethod
    def _embed_env() -> dict[str, str]:
        return {
            "LOCAL_EMBED_ENABLED": "true",
            "LOCAL_EMBED_BASE_URL": "http://ai-server:8001/v1",
            "LOCAL_EMBED_MODEL": "local-embed",
            "LOCAL_EMBED_API_KEY": "local-key",
        }

    def test_disabled_embed_returns_none(self) -> None:
        with patch.dict(os.environ, {"LOCAL_EMBED_ENABLED": "false"}, clear=False):
            self.assertFalse(openai_compatible.is_local_embed_enabled())
            self.assertIsNone(openai_compatible.embed_text("hi"))

    def test_embed_text_parses_openai_payload(self) -> None:
        response = _Response({"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        with patch.dict(os.environ, self._embed_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
            return_value=response,
        ) as post:
            vec = openai_compatible.embed_text("привет")

        self.assertEqual(vec, [0.1, 0.2, 0.3])
        self.assertEqual(post.call_args.kwargs["json"]["model"], "local-embed")
        self.assertEqual(post.call_args.kwargs["json"]["input"], "привет")

    def test_embed_text_returns_none_on_error(self) -> None:
        with patch.dict(os.environ, self._embed_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.requests.post",
            side_effect=openai_compatible.requests.RequestException("down"),
        ):
            self.assertIsNone(openai_compatible.embed_text("x"))

    def test_rag_get_embedding_uses_endpoint_no_legacy_fallback(self) -> None:
        """When the endpoint is enabled, get_embedding must use it and must NOT
        fall back to a different vector source."""
        from app.application.rag_memory import runtime as rag_runtime

        with patch.dict(os.environ, self._embed_env(), clear=False), patch(
            "app.infrastructure.llm.openai_compatible.embed_text",
            return_value=[0.5, 0.6],
        ) as embed:
            vec = rag_runtime.get_embedding(embed_model="local-embed", text="t")

        self.assertEqual(vec, [0.5, 0.6])
        embed.assert_called_once()

    def test_rag_get_embedding_returns_none_when_endpoint_disabled(self) -> None:
        from app.application.rag_memory import runtime as rag_runtime

        with patch.dict(os.environ, {"LOCAL_EMBED_ENABLED": "false"}, clear=False):
            vec = rag_runtime.get_embedding(embed_model="local-embed", text="t")

        self.assertIsNone(vec)


if __name__ == "__main__":
    unittest.main()
