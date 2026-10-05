"""Optional structured output reaches the existing wire paths without policy changes."""
from copy import deepcopy
import json

import pytest

from app.infrastructure.llm import openai_compatible as provider


MESSAGES = [{"role": "user", "content": "Return JSON."}]
FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "source_first_answer",
        "strict": True,
        "description": "Literal read-source selections.",
        "schema": {
            "type": "object",
            "properties": {"facts": {"type": "array", "items": {
                "type": "object", "properties": {"quote": {"type": "string"}},
            }}},
            "required": ["facts"],
            "additionalProperties": False,
        },
    },
}


class Response:
    def __init__(self):
        self.closed = False

    def raise_for_status(self):
        pass

    def json(self):
        return {"model": "local-model", "choices": [{"message": {
            "role": "assistant", "content": '{"facts":[]}',
        }}]}

    def iter_lines(self, decode_unicode=False):
        yield ("data: " + json.dumps({"choices": [{"delta": {
            "content": '{"facts":[]}',
        }}]})).encode("utf-8")
        yield b"data: [DONE]"

    def close(self):
        self.closed = True


@pytest.fixture
def transport(monkeypatch):
    config = provider.OpenAICompatibleConfig(True, "llama_server", "http://test/v1",
        "local-model", "local-key", 12.0, None, 131072)
    monkeypatch.setattr(provider, "local_llm_config", lambda: config)
    calls = []
    response = Response()

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(provider.requests, "post", post)
    return calls, response


def invoke(mode, options, *, tools=None):
    kwargs = {"model": "local-model", "messages": MESSAGES, "options": options}
    if mode == "sync":
        return provider.chat_completion(**kwargs, tools=tools)["message"]["content"]
    if mode == "tokens":
        return "".join(provider.chat_completion_stream(**kwargs))
    events = list(provider.chat_completion_event_stream(**kwargs, tools=tools))
    return next(event["response"]["message"]["content"] for event in events
                if event["type"] == "message")


@pytest.mark.parametrize("mode", ["sync", "tokens", "events"])
def test_schema_is_forwarded_at_top_level_and_deeply_detached(mode, transport):
    calls, response = transport
    response_format = deepcopy(FORMAT)
    assert invoke(mode, {"response_format": response_format}) == '{"facts":[]}'
    payload = calls[0][1]["json"]
    expected = {"model": "local-model", "messages": MESSAGES, "cache_prompt": True,
                "response_format": FORMAT}
    if mode != "sync":
        expected["stream"] = True
    if mode == "events":
        expected["stream_options"] = {"include_usage": True}
    assert payload == expected
    assert "options" not in payload and "tool_choice" not in payload
    assert payload["response_format"] is not response_format
    payload["response_format"]["json_schema"]["schema"]["properties"]["facts"]["items"]["properties"]["quote"]["type"] = "number"
    assert response_format == FORMAT
    if mode != "sync":
        assert response.closed


@pytest.mark.parametrize("mode", ["sync", "tokens", "events"])
def test_absent_response_format_preserves_the_exact_default_payload(mode, transport):
    calls, _ = transport
    assert invoke(mode, {}) == '{"facts":[]}'
    expected = {"model": "local-model", "messages": MESSAGES, "cache_prompt": True}
    if mode != "sync":
        expected["stream"] = True
    if mode == "events":
        expected["stream_options"] = {"include_usage": True}
    assert calls[0][1]["json"] == expected


@pytest.mark.parametrize("mode", ["sync", "events"])
def test_format_does_not_hide_tools_or_alter_existing_reasoning_options(mode, transport):
    calls, _ = transport
    tools = [{"type": "function", "function": {"name": "web_search"}}]
    options = {"response_format": FORMAT, "temperature": 0.45,
               "reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False}}
    invoke(mode, options, tools=tools)
    payload = calls[0][1]["json"]
    assert payload["tools"] == tools and payload["response_format"] == FORMAT
    assert payload["temperature"] == 0.45 and payload["reasoning_effort"] == "none"
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "tool_choice" not in payload


@pytest.mark.parametrize("mode", ["sync", "tokens", "events"])
@pytest.mark.parametrize("invalid", [
    None, "json_schema", [], {}, {"type": "json_object"},
    {"type": "json_schema", "json_schema": None},
    {"type": "json_schema", "json_schema": {"name": "", "schema": {}}},
    {"type": "json_schema", "json_schema": {"name": "   ", "schema": {}}},
    {"type": "json_schema", "json_schema": {"name": True, "schema": {}}},
    {"type": "json_schema", "json_schema": {"name": "facts"}},
    {"type": "json_schema", "json_schema": {"name": "facts", "schema": []}},
    {"type": "json_schema", "json_schema": {"name": "facts", "schema": True}},
    {"type": "json_schema", "json_schema": {"name": "facts", "schema": {"minimum": float("nan")}}},
    {"type": "json_schema", "json_schema": {"name": "facts", "schema": {1: {"type": "string"}}}},
    {"type": "json_schema", "json_schema": {"name": "facts", "schema": {"required": ("facts",)}}},
])
def test_invalid_explicit_format_is_rejected_locally_without_http(mode, invalid, transport):
    calls, _ = transport
    with pytest.raises(ValueError, match="response_format"):
        invoke(mode, {"response_format": invalid})
    assert calls == []


def test_cancellable_sync_delegation_keeps_schema_on_the_sse_request(transport):
    calls, response = transport
    handle = provider.LLMStreamCancelHandle()
    assert invoke("sync", {"response_format": FORMAT, "_stream_cancel_handle": handle}) == '{"facts":[]}'
    payload = calls[0][1]["json"]
    assert payload["response_format"] == FORMAT and payload["stream"] is True
    assert calls[0][1]["stream"] is True and response.closed
    assert not handle.is_closed


def test_schema_stream_remains_owned_and_interruptible_by_existing_cancel_handle(transport):
    calls, response = transport
    handle = provider.LLMStreamCancelHandle()
    stream = provider.chat_completion_event_stream(model="local-model", messages=MESSAGES,
        options={"response_format": FORMAT, "_stream_cancel_handle": handle})
    assert next(stream)["type"] == "delta"
    assert calls[0][1]["json"]["response_format"] == FORMAT
    handle.close()
    assert response.closed and handle.is_closed
    stream.close()
