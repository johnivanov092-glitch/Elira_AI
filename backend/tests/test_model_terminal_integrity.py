"""Incomplete provider output must never become an accepted answer or tool batch."""
from __future__ import annotations

from copy import deepcopy
import json
import socket

import pytest
import requests

from app.application.code_agent.agent_loop import run_code_agent, stream_code_agent
from app.application.code_agent.run_journal import RunJournal
from app.infrastructure.llm import openai_compatible as provider
from test_openai_compatible_provider import _Response, _llama_env
from test_qwen_protocol_roundtrip import _http_response
from test_web_search_engine_warnings import _http


@pytest.mark.parametrize("streaming", [False, True], ids=["sync", "stream"])
@pytest.mark.parametrize("finish_reason", ["stop", "tool_calls", "length", "upstream-specific"])
def test_provider_preserves_actual_finish_reason(monkeypatch, streaming, finish_reason):
    for key, value in _llama_env().items():
        monkeypatch.setenv(key, value)
    payload = {"choices": [{"message": {"content": "partial"}, "finish_reason": finish_reason}]}
    lines = [
        'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}',
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": finish_reason}]}),
        'data: {"choices":[],"usage":{"completion_tokens":1}}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(provider.requests, "post", lambda *a, **kw: _Response(payload, lines))
    kwargs = {"model": "local-model", "messages": [{"role": "user", "content": "Explain."}]}
    if streaming:
        response = list(provider.chat_completion_event_stream(**kwargs))[-1]["response"]
    else:
        response = provider.chat_completion(**kwargs)
    assert response.get("finish_reason") == finish_reason
    assert response["message"]["content"] == "partial"


def _tool(name, arguments, *, identifier="read-1"):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": arguments}}


def _run_http_case(tmp_path, monkeypatch, *, streaming, terminal, collect=False, saved_lines=None):
    """Real provider/PreparedRequest and file handler; only HTTP transport is replaced."""
    endpoint = "http://terminal-integrity.invalid"
    for key, value in _llama_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LLAMA_SERVER_BASE_URL", endpoint + "/v1")
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(provider, "_props_ctx_cache", {})
    (tmp_path / "input.txt").write_text("value=42", encoding="utf-8", newline="\n")
    payloads = []
    preamble = "I will read the input before answering."
    first = {"content": preamble, "tool_calls": [_tool("read_file", '{"path":"input.txt"}')],
             "finish_reason": "tool_calls"}
    replies = [first, terminal]

    def no_network(*args, **kwargs):
        pytest.fail("Offline terminal-integrity regression attempted a real network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    def send(session, request, **kwargs):
        if request.method == "GET" and request.url == endpoint + "/props":
            return _http_response(request, '{"n_ctx":65536}')
        assert (request.method, request.url) == ("POST", endpoint + "/v1/chat/completions")
        payloads.append(json.loads(request.body))
        assert len(payloads) <= len(replies), "Incomplete output triggered another model request"
        reply = replies[len(payloads) - 1]
        message = {key: deepcopy(value) for key, value in reply.items() if key != "finish_reason"}
        if not payloads[-1].get("stream"):
            body = {"model": "local-model", "choices": [{"message": message,
                     "finish_reason": reply.get("finish_reason")}], "usage": {"completion_tokens": 1}}
            return _http_response(request, json.dumps(body))
        delta = deepcopy(message)
        for index, call in enumerate(delta.get("tool_calls", [])):
            call["index"] = index
        lines = ["data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": None}]}),
                 "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": reply.get("finish_reason")}]}),
                 'data: {"choices":[],"usage":{"completion_tokens":1}}', "data: [DONE]"]
        if saved_lines is not None and len(payloads) == 2:
            lines = saved_lines
        return _http_response(request, "\n".join(lines) + "\n", sse=True)

    monkeypatch.setattr(requests.Session, "send", send)

    def sync_chat(**kwargs):
        options = dict(kwargs.get("options") or {})
        options.pop("_stream_cancel_handle", None)
        return provider.chat_completion(**{**kwargs, "options": options})

    kwargs = dict(user_message="Read input.txt and report its value.", project_root=tmp_path,
                  model="local-model", reasoning_effort="none", auto_remember=False,
                  permission_mode="accept_edits", base_tools=["read_file", "write_file"],
                  num_ctx=65536, run_id="terminal-" + tmp_path.name)
    if not streaming:
        kwargs["chat_fn"] = sync_chat
    output = run_code_agent(**kwargs) if collect else list(stream_code_agent(**kwargs))
    return output, payloads, preamble


@pytest.mark.parametrize("streaming", [False, True], ids=["sync", "stream"])
@pytest.mark.parametrize("case", ["empty-stop", "empty-length", "partial-length", "call-length",
                                  "malformed-call-length", "inline-call-length", "raw-think-length",
                                  "unclosed-think-length"])
def test_incomplete_turn_is_partial_resumable_without_dispatch(tmp_path, monkeypatch, streaming, case):
    terminal = {"content": "", "finish_reason": "stop" if case == "empty-stop" else "length"}
    if case == "partial-length":
        terminal["content"] = "The file contains value=42; further explanation is"
        terminal["reasoning_content"] = "Private reasoning must stay separate."
    elif case in {"call-length", "malformed-call-length"}:
        arguments = '{"path":"unsafe.txt","content":"should not be written"}'
        if case == "malformed-call-length":
            arguments = arguments[:-2]
        terminal["tool_calls"] = [_tool("write_file", arguments, identifier="incomplete-write")]
    elif case == "inline-call-length":
        terminal["content"] = '<tool_call>{"name":"write_file","arguments":{"path":"unsafe.txt","content":"x"}}</tool_call>'
    elif case == "raw-think-length":
        terminal["content"] = "Public prefix. <ThInK>" + "private reasoning " * 8 + "</tHiNk>current partial"
    elif case == "unclosed-think-length":
        terminal["content"] = "Public prefix.<think>" + "private reasoning " * 8
    events, payloads, preamble = _run_http_case(tmp_path, monkeypatch, streaming=streaming, terminal=terminal)
    assert len(payloads) == 2
    assert not (tmp_path / "unsafe.txt").exists()
    assert [event["tool"] for event in events if event["type"] == "tool_call"] == ["read_file"]
    assert not any(event["type"] == "final_response" for event in events)
    done = events[-1]
    assert done["type"] == "done" and done["ok"] is False
    assert done["stop_reason"] == "error" and done["answer_status"] == "degraded"
    assert done["partial"] is True and done["resumable"] is True
    assert done["completion_status"] == "partial" and done["criteria_confirmed"] is False
    step = done["steps"]
    visible = "".join(event["text"] for event in events if event["type"] == "delta" and event["step"] == step)
    if case == "partial-length":
        assert visible == terminal["content"]
    elif case == "raw-think-length":
        assert visible == "Public prefix. current partial"
    elif case == "unclosed-think-length":
        assert visible == "Public prefix."
    assert preamble not in visible and "Private reasoning" not in visible
    assert "private reasoning" not in visible and "<think>" not in visible.lower()
    journal = RunJournal.load(done["run_id"])
    assert journal.state["resumable"] is True
    assert journal.state["status"] != "completed"


@pytest.mark.parametrize("streaming", [False, True], ids=["sync", "stream"])
def test_clean_answer_after_real_read_is_unchanged(tmp_path, monkeypatch, streaming):
    events, payloads, _ = _run_http_case(
        tmp_path, monkeypatch, streaming=streaming,
        terminal={"content": "The file contains value=42.", "finish_reason": "stop"},
    )
    assert len(payloads) == 2
    assert next(event["text"] for event in events if event["type"] == "final_response") == "The file contains value=42."
    assert events[-1]["ok"] is True and events[-1]["answer_status"] == "complete"
    assert events[-1]["resumable"] is False


@pytest.mark.parametrize("streaming", [False, True], ids=["sync", "stream"])
@pytest.mark.parametrize("text", ["", "The file contains value=42; the explanation continues"])
def test_sync_api_preserves_only_current_partial_text(tmp_path, monkeypatch, streaming, text):
    result, payloads, preamble = _run_http_case(
        tmp_path, monkeypatch, streaming=streaming, collect=True,
        terminal={"content": text, "finish_reason": "length"},
    )
    assert len(payloads) == 2
    assert result["response"] == text and preamble not in result["response"]
    assert result["ok"] is False and result["partial"] is True
    assert result["answer_status"] == "degraded" and result["completion_status"] == "partial"
