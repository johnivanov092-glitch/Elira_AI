"""Retrieval recovery keeps independent operations available without synthesis turns."""
from copy import deepcopy
import json

import pytest
import requests as http_requests

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.tools import _web
from app.infrastructure.search.web_runtime import PageFetchResult
from test_web_search_engine_warnings import URL, _http


def test_repeated_operation_keeps_other_queries_available_without_forced_summary(tmp_path):
    turns = []

    def chat(**kwargs):
        turns.append(deepcopy(kwargs["messages"]))
        assert len(turns) <= 5, "Recovery must not insert a mandatory synthesis turn"
        names = {schema["function"]["name"] for schema in kwargs["tools"]}
        assert "web_search" in names, "A refused query must not hide other queries"
        assert not any("один промежуточный шаг без инструментов" in str(m.get("content", ""))
                       for m in kwargs["messages"])
        if len(turns) == 5:
            return {"message": {"content": f"Найдена документация: [Источник]({URL}). Содержимое страницы пока не проверено."}}
        return {"message": {"tool_calls": [{"id": str(len(turns)), "function": {
            "name": "web_search", "arguments": {"query": "service documentation" if len(turns) < 4 else "service reference"},
        }}]}}

    with _http({"results": [{"url": URL, "title": "Documentation"}], "unresponsive_engines": []}) as requests:
        events = list(stream_code_agent(user_message="Найди документацию сервиса.", project_root=tmp_path,
            chat_fn=chat, permission_mode="bypass", auto_remember=False,
            base_tools=["web_search"], num_ctx=65536))
    assert len(requests) == 2
    assert any(event.get("status") == "strategy_required" for event in events)
    assert events[-1]["stop_reason"] == "answer"


def test_encoded_and_native_lists_share_repeat_guard_before_execution(tmp_path):
    turns = []

    def chat(**kwargs):
        turns.append(deepcopy(kwargs["messages"]))
        assert len(turns) <= 3
        if len(turns) == 3:
            return {"message": {"content": f"Найдена документация: [Источник]({URL}). Содержимое страницы пока не проверено."}}
        queries = ["service documentation"]
        return {"message": {"tool_calls": [{"id": str(len(turns)), "function": {
            "name": "web_search", "arguments": {"queries": json.dumps(queries) if len(turns) == 1 else queries},
        }}]}}

    with _http({"results": [{"url": URL, "title": "Documentation"}], "unresponsive_engines": []}) as requests:
        events = list(stream_code_agent(user_message="Найди документацию сервиса.", project_root=tmp_path,
            chat_fn=chat, permission_mode="bypass", auto_remember=False,
            base_tools=["web_search"], num_ctx=65536))
    assert len(requests) == 1
    calls = [event for event in events if event["type"] == "tool_call"]
    assert all(event["arguments"]["queries"] == ["service documentation"] for event in calls)
    assert calls[-1]["error"] == "repeated_web_without_progress"
    assert not calls[-1]["dispatched"]
    assert events[-1]["stop_reason"] == "answer"


@pytest.mark.parametrize("original_tool", ["web_search", "web_fetch"])
def test_successful_operation_is_not_dispatched_again_after_other_source(tmp_path, monkeypatch, original_tool):
    turns, reads = [], []
    original_url, another_url = "https://example.org/original", "https://example.org/another"

    def fetch(url, limit):
        reads.append(url)
        text = "Original evidence." if url == original_url else "Another source evidence."
        return PageFetchResult(text=text, final_url=url, status_code=200, mime="text/html")

    monkeypatch.setattr(_web, "_fetch_one", fetch)
    original = {"query": "original source"} if original_tool == "web_search" else {"url": original_url}
    another = {"query": "another source"} if original_tool == "web_search" else {"url": another_url}
    operations = [(original_tool, original), ("web_search", {"query": "different evidence"}),
                  (original_tool, original), (original_tool, another)]

    def chat(**kwargs):
        turns.append(deepcopy(kwargs["messages"]))
        assert len(turns) <= 5, "Recovery must not insert a mandatory synthesis turn"
        names = {schema["function"]["name"] for schema in kwargs["tools"]}
        assert original_tool in names
        if len(turns) == 5:
            content = "Проверен исходный источник." if original_tool == "web_fetch" else (
                f"Найдены ссылки: [Источник]({original_url}). Содержимое страницы пока не проверено.")
            return {"message": {"content": content}}
        tool, args = operations[len(turns) - 1]
        return {"message": {"tool_calls": [{"id": str(len(turns)), "function": {
            "name": tool, "arguments": args}}]}}

    def reply(params):
        url = original_url if params["q"] == "original source" else (
            another_url if params["q"] == "another source" else "https://example.org/different")
        return {"results": [{"url": url, "title": params["q"], "content": params["q"]}],
                "unresponsive_engines": []}

    with _http(reply=reply) as requests:
        events = list(stream_code_agent(user_message="Собери источники для сравнения.", project_root=tmp_path,
            chat_fn=chat, permission_mode="bypass", auto_remember=False,
            base_tools=["web_search", "web_fetch"], num_ctx=65536))
    calls = [event for event in events if event["type"] == "tool_call"]
    assert len(calls) == 4
    assert calls[0].get("dispatched", True) and calls[1].get("dispatched", True)
    assert calls[2]["error"] == "repeated_web_without_progress" and not calls[2]["dispatched"]
    assert calls[3].get("dispatched", True) and calls[3]["ok"]
    assert [call["params"]["q"] for call in requests] == (
        ["original source", "different evidence", "another source"] if original_tool == "web_search" else ["different evidence"])
    assert reads == ([] if original_tool == "web_search" else [original_url, another_url])
    assert len(turns) == 5 and events[-1]["stop_reason"] == "answer"


@pytest.mark.parametrize("name,field", [("web_search", "queries"), ("web_fetch", "urls")])
def test_malformed_batch_is_rejected_before_dispatch(tmp_path, name, field):
    def chat(**kwargs):
        return {"message": {"tool_calls": [{"id": "malformed", "function": {
            "name": name, "arguments": {field: "[invalid"},
        }}]}}

    stream = stream_code_agent(user_message="Прочитай веб-источники.", project_root=tmp_path,
        chat_fn=chat, permission_mode="bypass", auto_remember=False,
        base_tools=[name], num_ctx=65536)
    try:
        event = next(event for event in stream if event["type"] == "tool_call")
    finally:
        stream.close()
    assert event["error"] == "argument_format"
    assert field in event["result"] and "array" in event["result"]
    assert not event["dispatched"]
    assert "is empty" not in event["result"]


@pytest.mark.parametrize("fallback", ["browser", "other_source"])
def test_first_actual_http403_advice_reaches_provider_and_keeps_fallback_available(tmp_path, monkeypatch, fallback):
    denied = "https://source.example.org/found-article"
    alternative = "https://source.example.org/found-reference"
    read_url = denied if fallback == "browser" else alternative
    turns, http_reads, browser_reads = [], [], []
    quote = "The service accepts UTF-8 JSON input."

    def fetch(url, **kwargs):
        http_reads.append(url)
        response = http_requests.Response()
        response.url, response.encoding = url, "utf-8"
        response.status_code = 403 if url == denied else 200
        response.headers["Content-Type"] = "text/html"
        response._content = ("Access denied" if url == denied else
                             "<main><h1>Service reference</h1><p>" + quote + "</p><p>" +
                             "Reference body. " * 100 + "</p></main>").encode("utf-8")
        response._content_consumed = True
        return response

    def render(url, *_args, **_kwargs):
        browser_reads.append(url)
        return "Service reference", url, quote, 0, None, 200

    monkeypatch.setattr("requests.get", fetch)
    monkeypatch.setattr("app.application.web.ssrf_guard.check_ssrf", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(_web, "_browser_render", render)

    def chat(**kwargs):
        turns.append(deepcopy(kwargs["messages"]))
        assert len(turns) <= 4, "Failure recovery must not add synthesis turns"
        names = {schema["function"]["name"] for schema in kwargs["tools"]}
        assert {"web_search", "web_fetch", "browser"} <= names
        if len(turns) == 3:
            failed = next(message["content"] for message in reversed(kwargs["messages"])
                          if message.get("role") == "tool")
            assert "HTTP 403" in failed and "не являются содержимым статьи" in failed
            assert "точный URL" in failed and "не угадывай" in failed and "browser" in failed
            name, args = ("browser", {"url": denied}) if fallback == "browser" else (
                "web_fetch", {"url": alternative})
        elif len(turns) == 4:
            shown = next(message["content"] for message in reversed(kwargs["messages"])
                         if message.get("role") == "tool")
            assert quote in shown
            return {"message": {"content": f"Сервис принимает UTF-8 JSON. [Источник]({read_url})."}}
        else:
            name, args = ("web_search", {"query": "service reference"}) if len(turns) == 1 else (
                "web_fetch", {"url": denied})
        return {"message": {"tool_calls": [{"id": str(len(turns)), "function": {
            "name": name, "arguments": args}}]}}

    with _http({"results": [{"url": denied, "title": "Service reference"},
                            {"url": alternative, "title": "Another service reference"}],
                "unresponsive_engines": []}) as searches:
        events = list(stream_code_agent(user_message="Найди и прочитай документацию сервиса.",
            project_root=tmp_path, chat_fn=chat, permission_mode="bypass", auto_remember=False,
            base_tools=["web_search", "web_fetch", "browser"], num_ctx=65536))
    calls = [event for event in events if event.get("type") == "tool_call"]
    assert len(calls) == 3 and len(searches) == 1
    assert calls[1]["ok"] is False and calls[2]["ok"] is True
    assert http_reads == ([denied] if fallback == "browser" else [denied, alternative])
    assert browser_reads == ([denied] if fallback == "browser" else [])
    assert not any(event.get("type") == "command_recovery" for event in events)
    assert not any(call.get("tool") == "runtime_control" for call in calls)
    final = next(event for event in events if event.get("type") == "final_response")
    assert read_url in final["text"] and final["answer_status"] == "complete"
    assert events[-1]["stop_reason"] == "answer" and len(turns) == 4
