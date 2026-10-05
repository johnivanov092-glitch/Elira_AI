"""Content visibility is independent of provider response and stream lifecycle."""
from __future__ import annotations

from copy import deepcopy
import threading

import pytest

from app.application.code_agent import model_turn
from app.infrastructure.llm.openai_compatible import LLMStreamCancelHandle


ANSWER = "Проверенный ответ остаётся целиком в provider response. " * 3
REASONING = "Проверяю источник."
QUOTED_XML = '<tool_call><function=web_fetch><parameter=url>https://example.org/data</parameter></function></tool_call>'


def _collect(stream):
    events = []
    while True:
        try:
            events.append(next(stream))
        except StopIteration as stopped:
            return events, stopped.value


def _turn(provider, *, kwargs=None, cancel_event=None, handle=None, **visibility):
    return model_turn.stream_model_turn(
        chat=lambda **kw: pytest.fail("unexpected synchronous provider call"),
        stream_chat=provider, kwargs=kwargs or {"model": "fixture", "messages": [], "tools": [], "options": {}},
        cancel_event=cancel_event or threading.Event(), upstream_cancel_handle=handle or LLMStreamCancelHandle(),
        step=7, **visibility,
    )


@pytest.mark.parametrize("visibility", [{}, {"emit_content_deltas": True}, {"emit_content_deltas": False}],
                         ids=["default", "explicit-visible", "hidden"])
def test_content_visibility_preserves_final_payload_reasoning_and_provider_arguments(visibility):
    kwargs = {"model": "fixture", "messages": [{"role": "user", "content": "Вопрос"}],
              "tools": [{"type": "function", "function": {"name": "web_fetch"}}],
              "options": {"reasoning_effort": "low", "temperature": 0.25}}
    original = deepcopy(kwargs)
    calls = []
    handle = LLMStreamCancelHandle()
    payload = {"message": {"content": ANSWER, "reasoning_content": REASONING, "tool_calls": []},
               "usage": {"prompt_tokens": 9, "completion_tokens": 12}}

    def provider(**kw):
        calls.append(kw)
        yield {"type": "reasoning", "content": REASONING}
        yield ANSWER[:41]
        yield {"type": "delta", "content": ANSWER[41:93]}
        yield {"type": "delta", "text": ANSWER[93:]}
        yield {"type": "message", "response": payload}

    events, response = _collect(_turn(provider, kwargs=kwargs, handle=handle, **visibility))

    assert response == payload
    assert [e for e in events if e["type"] == "reasoning_delta"] == [
        {"type": "reasoning_delta", "step": 7, "text": REASONING}]
    deltas = [e for e in events if e["type"] == "delta"]
    if visibility.get("emit_content_deltas", True):
        assert "".join(e["text"] for e in deltas) == ANSWER
        assert all(e["answer_state"] == "draft" and e["step"] == 7 for e in deltas)
    else:
        assert deltas == []
    assert len(calls) == 1 and calls[0]["options"]["_stream_cancel_handle"] is handle
    normalized = {**calls[0], "options": {k: v for k, v in calls[0]["options"].items()
                                         if k != "_stream_cancel_handle"}}
    assert normalized == original and kwargs == original
    assert "emit_content_deltas" not in calls[0]


@pytest.mark.parametrize("explicit_final", [False, True], ids=["aggregated-response", "explicit-response"])
def test_hidden_json_and_quoted_xml_remain_data_in_returned_response(monkeypatch, explicit_final):
    text = '{"quote": "' + QUOTED_XML + '", "answer": "исходный текст"}'

    def unexpected_parse(*args, **kwargs):
        pytest.fail("hidden provider content must not trigger inline-call parsing")

    monkeypatch.setattr(model_turn, "_contains_tool_trace", unexpected_parse)
    monkeypatch.setattr(model_turn, "_extract_inline_tool_calls", unexpected_parse)

    def provider(**kw):
        for chunk in (text[:58], text[58:100], text[100:]):
            yield {"type": "delta", "content": chunk}
        if explicit_final:
            yield {"type": "message", "response": {"message": {"content": text, "tool_calls": []}}}

    events, response = _collect(_turn(provider, emit_content_deltas=False))
    assert not [e for e in events if e["type"] == "delta"]
    assert response["message"]["content"] == text and QUOTED_XML in response["message"]["content"]
    assert response["message"]["tool_calls"] == []


def test_hidden_content_keeps_heartbeat_and_final_response(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    monkeypatch.setattr(model_turn, "_LLM_HEARTBEAT_EVERY", 0.01)

    def provider(**kw):
        try:
            yield {"type": "reasoning", "content": REASONING}
            assert release.wait(2), "test never released provider"
            yield {"type": "delta", "content": ANSWER}
        finally:
            finished.set()

    stream = _turn(provider, emit_content_deltas=False)
    try:
        assert next(stream) == {"type": "reasoning_delta", "step": 7, "text": REASONING}
        assert next(stream) == {"type": "heartbeat", "step": 7}
        release.set()
        events, response = _collect(stream)
        assert not [e for e in events if e["type"] == "delta"]
        assert response["message"]["content"] == ANSWER
        assert finished.wait(0.5)
    finally:
        release.set()
        stream.close()


def test_hidden_content_cancellation_closes_upstream_and_discards_late_response(monkeypatch):
    cancel = threading.Event()
    closed = threading.Event()
    finished = threading.Event()
    monkeypatch.setattr(model_turn, "_LLM_HEARTBEAT_EVERY", 0.01)

    class Transport:
        def close(self):
            closed.set()

    def provider(**kw):
        kw["options"]["_stream_cancel_handle"].bind(Transport())
        try:
            yield {"type": "delta", "content": ANSWER}
            assert closed.wait(2), "cancellation never closed upstream"
            yield {"type": "message", "response": {"message": {"content": "late answer"}}}
        finally:
            finished.set()

    stream = _turn(provider, cancel_event=cancel, emit_content_deltas=False)
    try:
        assert next(stream) == {"type": "heartbeat", "step": 7}
        cancel.set()
        events, response = _collect(stream)
        assert events == [] and response == {}
        assert closed.wait(0.5) and finished.wait(0.5)
    finally:
        cancel.set()
        closed.set()
        stream.close()


def test_hidden_content_does_not_swallow_provider_failure():
    def provider(**kw):
        yield {"type": "delta", "content": ANSWER}
        raise RuntimeError("fixture transport failed")

    with pytest.raises(RuntimeError, match="fixture transport failed"):
        _collect(_turn(provider, emit_content_deltas=False))


def test_hidden_content_preserves_synchronous_provider_response():
    payload = {"message": {"content": '{"quote": "' + QUOTED_XML + '"}', "tool_calls": []}}
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        return payload

    stream = model_turn.stream_model_turn(chat=chat, stream_chat=None,
        kwargs={"model": "fixture", "messages": [], "tools": [], "options": {}},
        cancel_event=threading.Event(), upstream_cancel_handle=LLMStreamCancelHandle(),
        step=3, emit_content_deltas=False)
    events, response = _collect(stream)
    assert events == [] and response == payload and len(calls) == 1
    assert "emit_content_deltas" not in calls[0]
