"""SearXNG engine diagnostics stay tool data and journal records, never answer text.

John (2026-10-05): the code does not append a «SearXNG: engine — error» block to the
model's answer. The model still sees the warnings in the web_search result and the
run journal keeps them for the administrator. HTTP and chat boundaries are offline
fixtures; native tools, executor, evidence, acceptance, coordinator and journal
remain the production implementations.
"""
import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.tools._web import tool_web_search
from app.infrastructure.search.web_search import search_web
from app.core import web_engines
from test_web_search_engine_warnings import URL, WARNINGS, _http, _payload


@pytest.mark.parametrize("answer", [
    f"Документация: [Источник]({URL})",
    f"Documentation: [Source]({URL})",
])
def test_engine_warning_reaches_model_and_journal_but_not_the_answer(tmp_path, answer):
    chats = []
    replies = iter([
        {"message": {"content": "", "tool_calls": [{"function": {
            "name": "web_search", "arguments": {"query": "service documentation"},
        }}]}},
        {"message": {"content": answer, "tool_calls": []}},
    ])

    def chat(**kwargs):
        chats.append(kwargs["messages"])
        return next(replies)

    with _http(_payload()) as calls:
        events = list(stream_code_agent(user_message="Найди документацию сервиса.", project_root=tmp_path,
                                        chat_fn=chat, permission_mode="bypass", auto_remember=False,
                                        num_ctx=65536))
    final = next(event for event in events if event["type"] == "final_response")
    assert len(calls) == 1 and len(chats) == 2
    assert final["text"] == answer and "SearXNG:" not in final["text"]
    tool_result = next(message["content"] for message in chats[1] if message.get("role") == "tool")
    assert "swisscows" in tool_result
    search = next(event for event in events if event["type"] == "tool_call" and event["tool"] == "web_search")
    assert search["engine_warnings"] == WARNINGS
    journal = RunJournal.load(events[-1]["run_id"]).events_path.read_text(encoding="utf-8")
    assert "swisscows" in journal and "HTTP 429" in journal


@pytest.mark.parametrize("boundary", ["provider", "facade", "single", "page", "batch"])
def test_actual_zero_results_failure_preserves_structured_warnings(boundary):
    payload = {"results": [], "unresponsive_engines": [["swisscows", "HTTP 429"]]}
    with _http(payload):
        if boundary in {"provider", "facade"}:
            with pytest.raises(RuntimeError) as error:
                (web_engines.search_searxng if boundary == "provider" else search_web)("service documentation")
            assert getattr(error.value, "engine_warnings", None) == WARNINGS
            return
        args = {"queries": ["first", "second"]} if boundary == "batch" else {
            "query": "service documentation", **({"page": 2} if boundary == "page" else {}),
        }
        output = tool_web_search(**args)
    assert output["ok"] is False
    warnings = [{"query": query, **WARNINGS[0]} for query in ["first", "second"]] if boundary == "batch" else WARNINGS
    assert output.get("engine_warnings") == warnings


def test_actual_batch_failure_warning_does_not_disappear_beside_success():
    def reply(params):
        return _payload() if params["q"] == "working" else {
            "results": [], "unresponsive_engines": [["mwmbl", "timeout"]],
        }
    with _http(reply=reply):
        output = tool_web_search(queries=["working", "failed"])
    assert output["ok"] is True and output["partial"] is True
    assert output["engine_warnings"] == [{"query": "working", **WARNINGS[0]},
                                         {"query": "failed", "engine": "mwmbl", "error": "timeout"}]


@pytest.mark.parametrize("results", [[], [{"url": URL, "title": "Documentation"}]])
def test_actual_healthy_search_does_not_add_false_warning(results):
    with _http({"results": results, "unresponsive_engines": []}):
        output = tool_web_search(query="service documentation")
    assert not output.get("engine_warnings")
