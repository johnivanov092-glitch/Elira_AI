"""Qwen history and sampling at the real OpenAI HTTP request boundary."""

from __future__ import annotations

from copy import deepcopy
import json
import socket

import pytest
import requests

from app.application.code_agent.agent_loop import stream_code_agent
from app.infrastructure.llm import openai_compatible as provider
from test_openai_compatible_provider import _Response, _llama_env


@pytest.mark.parametrize("streaming", [False, True], ids=["sync", "stream"])
def test_explicit_top_p_and_assistant_reasoning_reach_next_request(monkeypatch, streaming):
    for key, value in _llama_env().items():
        monkeypatch.setenv(key, value)
    reasoning = "Read the original value before calculating."
    messages = [
        {"role": "user", "content": "Read input.txt."},
        {"role": "assistant", "content": "", "reasoning_content": reasoning,
         "tool_calls": [{"id": "read-1", "type": "function", "function": {
             "name": "read_file", "arguments": {"path": "input.txt"},
         }}]},
        {"role": "tool", "tool_call_id": "read-1", "content": "value=42"},
    ]
    original = deepcopy(messages)
    seen = []

    def post(*args, **kwargs):
        seen.append(kwargs["json"])
        return _Response({"choices": [{"message": {"content": "42"}}]}, lines=[
            'data: {"choices":[{"delta":{"content":"42"}}]}', "data: [DONE]",
        ])

    monkeypatch.setattr(provider.requests, "post", post)
    options = {"temperature": 0, "top_p": 0.8}
    if streaming:
        list(provider.chat_completion_event_stream(model="local-model", messages=messages, options=options))
    else:
        provider.chat_completion(model="local-model", messages=messages, options=options)
    assert seen[0].get("top_p") == 0.8
    assert seen[0]["temperature"] == 0
    assistant = seen[0]["messages"][1]
    assert assistant.get("reasoning_content") == reasoning
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"path": "input.txt"}
    assert seen[0]["messages"][2]["tool_call_id"] == "read-1"
    assert messages == original


def test_explicit_tool_result_consumes_its_pending_id_before_legacy_result():
    messages = [
        {"role": "user", "content": "Read both files."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "known", "function": {"name": "read_file", "arguments": {"path": "first"}}},
            {"function": {"name": "read_file", "arguments": {"path": "second"}}},
        ]},
        {"role": "tool", "tool_call_id": "known", "content": "first result"},
        {"role": "tool", "content": "second result"},
    ]
    wire = provider._normalize_messages_for_request(messages)
    call_ids = [call["id"] for call in wire[1]["tool_calls"]]
    assert [item["tool_call_id"] for item in wire[2:]] == call_ids
    assert len(set(call_ids)) == 2


def _http_response(request, data, *, sse=False):
    response = requests.Response()
    response.status_code = 200
    response.request = request
    response.url = request.url
    response.headers["Content-Type"] = "text/event-stream" if sse else "application/json"
    response._content = data.encode("utf-8")
    response._content_consumed = True
    return response


@pytest.mark.parametrize("reasoning_field", ["reasoning_content", "reasoning"])
@pytest.mark.parametrize("effort", ["none", "low"])
@pytest.mark.parametrize("structured", [False, True], ids=["simple", "planned"])
def test_agent_preserves_reasoning_across_real_tool_dispatch_and_prepared_request(
    tmp_path, monkeypatch, reasoning_field, effort, structured,
):
    endpoint = "http://qwen-protocol.invalid"
    monkeypatch.setenv("LLAMA_SERVER_ENABLED", "true")
    monkeypatch.setenv("LLAMA_SERVER_BASE_URL", endpoint + "/v1")
    monkeypatch.setenv("LLAMA_SERVER_MODEL", "local-model")
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(provider, "_props_ctx_cache", {})
    (tmp_path / "input.txt").write_text("value=42", encoding="utf-8")
    payloads = []
    reasoning = "I must read input.txt before reporting its value."
    planned = structured and effort != "none"
    plan = {"goal": "Read input.txt", "current_state": "The file exists",
            "ordered_steps": ["Read input.txt"], "acceptance_checks": ["input.txt exists"],
            "risks": [], "capability_groups": [], "current_step": 1}

    def no_network(*args, **kwargs):
        pytest.fail("Offline protocol regression attempted a real network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    def send(session, request, **kwargs):
        if request.method == "GET" and request.url == endpoint + "/props":
            response = _http_response(request, '{"detail":"Not Found"}')
            response.status_code = 404
            return response
        if request.method == "GET" and request.url == endpoint + "/v1/models":
            return _http_response(request, '{"data":[{"id":"local-model","owned_by":"vllm","max_model_len":65536}]}')
        assert (request.method, request.url) == ("POST", endpoint + "/v1/chat/completions")
        payloads.append(json.loads(request.body))
        assert len(payloads) <= 2 + planned, "Unexpected additional coordinator turn"
        if planned and len(payloads) == 1:
            assert not payloads[-1].get("tools"), "Planner must not dispatch tools"
            delta = {"content": json.dumps(plan)}
        elif len(payloads) == 1 + planned:
            delta = {"tool_calls": [{
                "index": 0, "id": "read-1", "type": "function", "function": {
                    "name": "read_file", "arguments": '{"path":"input.txt"}',
                },
            }]}
            if effort != "none":
                delta[reasoning_field] = reasoning
        else:
            delta = {"content": "The file contains value=42."}
        return _http_response(request, "data: " + json.dumps({"choices": [{"delta": delta}]})
                              + "\n\ndata: [DONE]\n\n", sse=True)

    monkeypatch.setattr(requests.Session, "send", send)
    prompt = "Read input.txt and report its exact value."
    if structured:
        prompt = "Цель: " + prompt + "\nКритерии готовности:\n- input.txt существует"
    events = list(stream_code_agent(
        user_message=prompt, project_root=tmp_path, model="local-model", reasoning_effort=effort,
        run_id="qwen-history-" + effort + "-" + reasoning_field + "-" + tmp_path.name,
        base_tools=["read_file"], read_only=True, auto_remember=False,
    ))
    assert len(payloads) == 2 + planned, events[-1]
    assert any(event["type"] == "planning_started" for event in events) is planned
    call = next(event for event in events if event["type"] == "tool_call")
    assert call["tool"] == "read_file" and call["ok"] is True
    messages = payloads[-1]["messages"]
    assistant = next(item for item in messages if item.get("tool_calls"))
    assert assistant.get("reasoning_content", "") == (reasoning if effort != "none" else "")
    tool = next(item for item in messages if item.get("tool_call_id") == "read-1")
    assert "value=42" in tool["content"]
    assert [item["content"] for item in messages if item["role"] == "user"] == [prompt]
    assert all(reasoning not in item.get("content", "") for item in messages)
    assert next(event["text"] for event in events if event["type"] == "final_response") == "The file contains value=42."
    for body in payloads:
        assert body["temperature"] == (1.0 if effort != "none" else 0.7)
        assert body.get("top_p") == (0.95 if effort != "none" else 0.8)
        assert body.get("top_k") == 20
        assert body.get("min_p") == 0.0
        assert body.get("presence_penalty") == (0.0 if effort != "none" else 1.5)
        assert body.get("repetition_penalty") == 1.0
        assert body["chat_template_kwargs"].get("preserve_thinking") is True
