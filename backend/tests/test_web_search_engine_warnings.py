"""Real search implementations, stubbed only at the SearXNG HTTP boundary."""
from contextlib import contextmanager
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.core import web_engines
from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool
from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.loop_helpers import WEB_TOOL_RESULT_LLM_LIMIT
from app.application.code_agent.run_observations import RunObservations
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.tools import build_tool_dispatch
from app.infrastructure.search.web_search import search_web as search_facade
from app.application.code_agent.tools._web import tool_web_search


URL = "https://security.example.org/advisory"
WARNINGS = [{"engine": "swisscows", "error": "HTTP 429"}]


def _payload(*, url=URL):
    return {"results": [{"url": url, "title": "Service security advisory", "content": "Vendor update"}],
            "unresponsive_engines": [["swisscows", "HTTP 429"]]}


@contextmanager
def _http(payload=None, *, reply=None):
    calls = []

    def get(url, *, params, timeout):
        calls.append({"url": url, "params": dict(params), "timeout": timeout})
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.encoding = "utf-8"
        response._content = json.dumps(reply(params) if reply else payload, ensure_ascii=False).encode("utf-8")
        return response

    client = MagicMock()
    client.get.side_effect = get
    with patch.dict("os.environ", {"SEARXNG_URL": "http://search.local"}), patch.object(
        web_engines, "session", return_value=client,
    ):
        yield calls


@pytest.mark.parametrize("boundary", ["provider", "facade", "native"])
def test_real_http_to_native_retains_engine_failure_with_useful_results(boundary):
    with _http(_payload()) as calls:
        if boundary == "provider":
            result = web_engines.search_searxng("service security advisory")
            assert result[0]["href"] == URL
            warnings = result.engine_warnings
        elif boundary == "facade":
            result = search_facade("service security advisory")
            assert result["ok"] is True and result["sources"][0]["href"] == URL
            warnings = result["engine_warnings"]
            assert "WARNING:" in result["context"]
        else:
            result = tool_web_search(query="service security advisory")
            assert result["ok"] is True and result["sources"][0]["url"] == URL
            warnings = result["engine_warnings"]
            assert "WARNING:" in result["text"]
            assert "swisscows" in result["text"] and "HTTP 429" in result["text"]
        assert warnings == WARNINGS
        assert len(calls) == 1 and calls[0]["timeout"] == 20


@pytest.mark.parametrize("category", ["general", "news", "it", "science", "images", "videos", "map", "music", "files"])
@pytest.mark.parametrize("time_range", ["day", "week", "month", "year"])
def test_warning_survives_categories_language_time_and_site_constraints(category, time_range):
    query = "обновление сервиса site:security.example.org -site:old.security.example.org"
    payload = _payload()
    payload["results"][0]["publishedDate"] = "2026-10-01T08:00:00Z"
    payload["results"].extend([
        {"url": "https://other.example.org/advisory", "title": "Other service"},
        {"url": "https://old.security.example.org/advisory", "title": "Old service"},
    ])
    with _http(payload) as calls:
        result = tool_web_search(query=query, categories=category, time_range=time_range, top_k=30)
    assert result["ok"] is True
    assert [source["url"] for source in result["sources"]] == [URL]
    assert result["engine_warnings"] == WARNINGS
    assert "WARNING:" in result["text"]
    assert calls[0]["params"] == {"q": query, "format": "json", "language": "ru",
                                  "categories": category, "time_range": time_range}
    assert calls[0]["timeout"] == 20


def test_page_two_keeps_warning_after_site_filter_and_exact_pagination_params():
    query = "обновление site:security.example.org"
    with _http(_payload()) as calls:
        result = tool_web_search(query=query, page=2, categories="news", time_range="month", top_k=30)
    assert result["ok"] is True and result["sources"][0]["url"] == URL
    assert result["engine_warnings"] == WARNINGS
    assert "WARNING:" in result["text"]
    assert calls[0]["params"] == {"q": query, "format": "json", "language": "ru",
                                  "categories": "news", "time_range": "month", "pageno": "2"}


def test_five_queries_ten_results_each_keep_receipts_warnings_and_text_budget():
    queries = [f"service-advisory-{index}" for index in range(5)]

    def reply(params):
        return {"results": [{"url": f"https://security.example.org/{params['q']}/{index}",
                             "title": params["q"], "content": "Advisory detail " * 30}
                            for index in range(10)],
                "unresponsive_engines": [["swisscows", "HTTP 429"]]}

    with _http(reply=reply) as calls:
        result = tool_web_search(queries=queries, top_k=10, categories="it", time_range="year")
    assert result["ok"] is True and not result.get("partial") and not result.get("query_errors")
    assert len(calls) == 5 and {call["params"]["q"] for call in calls} == set(queries)
    assert all(call["params"]["categories"] == "it" and call["params"]["time_range"] == "year"
               and call["timeout"] == 20 for call in calls)
    assert len(result["sources"]) == 50
    assert len(result["query_sources"]) == 5
    assert all(len(row["source_ids"]) == 10 for row in result["query_sources"])
    assert result["engine_warnings"] == [{"query": query, **WARNINGS[0]} for query in queries]
    assert len(result["text"]) <= WEB_TOOL_RESULT_LLM_LIMIT and "WARNING:" in result["text"]
    assert "5 engine failures" in result["text"]


@pytest.mark.parametrize("args", [{"query": "empty"}, {"queries": ["empty", "also empty"]},
                                  {"query": "empty", "page": 2}])
@pytest.mark.parametrize("known_failure", [False, True])
def test_known_engine_failure_is_not_healthy_empty(args, known_failure):
    payload = {"results": [], "unresponsive_engines": [["swisscows", "HTTP 429"]] if known_failure else []}
    with _http(payload):
        result = tool_web_search(**args)
    assert result["ok"] is (not known_failure)
    if known_failure:
        assert "HTTP 429" in result["text"] and "ERROR:" in result["text"]
        assert "No web results" not in result["text"]
    else:
        assert not result.get("engine_warnings") and not result.get("query_errors")


@pytest.mark.parametrize("args", [{"query": "service site:unmatched.example.org"},
                                  {"queries": ["service site:unmatched.example.org"]},
                                  {"query": "service site:unmatched.example.org", "page": 2}])
def test_site_filtered_empty_partial_result_keeps_coverage_unknown(args):
    with _http(_payload()):
        result = tool_web_search(**args)
    assert result["ok"] is True and not result.get("sources")
    assert result["engine_warnings"]
    assert "WARNING:" in result["text"]
    assert "No web results" not in result["text"]
    assert "дальше результатов нет" not in result["text"]


@pytest.mark.parametrize("warnings", [None, {}, [["engine"]], [["engine", "failure", "extra"]],
                                      [[7, "failure"]], [["engine", ""]]])
def test_malformed_upstream_failure_metadata_fails_explicitly(warnings):
    payload = _payload()
    payload["unresponsive_engines"] = warnings
    with _http(payload):
        result = tool_web_search(query="advisory")
    assert result["ok"] is False
    assert "Invalid SearXNG response" in result["text"]


def test_no_usable_provider_links_with_failures_does_not_claim_success():
    with _http(_payload(url="javascript:alert(1)")):
        result = tool_web_search(query="advisory")
    assert result["ok"] is False and "HTTP 429" in result["text"]


def test_full_structured_warnings_survive_bounded_redacted_text_summary():
    payload = _payload()
    payload["unresponsive_engines"] = [[f"engine-{index}", "HTTP 429 token=private-canary"] for index in range(6)]
    with _http(payload):
        result = tool_web_search(query="advisory")
    assert len(result["engine_warnings"]) == 6
    assert "6 engine failures" in result["text"]
    assert "private-canary" not in str(result)
    assert all("[REDACTED]" in warning["error"] for warning in result["engine_warnings"])


def test_actual_native_query_bindings_and_warnings_reach_sse_and_durable_trace(tmp_path):
    queries = ["service security advisory", "service maintenance documentation"]
    responses = iter([
        {"message": {"content": "", "tool_calls": [{"function": {
            "name": "web_search", "arguments": {"queries": queries, "categories": "it"},
        }}]}},
        {"message": {"content": "Поиск выполнен; часть движков ответила HTTP 429.", "tool_calls": []}},
    ])
    messages = []

    def chat(**kwargs):
        messages.append(kwargs["messages"])
        return next(responses)

    with _http(reply=lambda params: _payload()) as calls:
        events = list(stream_code_agent(user_message="Найди документацию сервиса.", project_root=tmp_path,
                                        chat_fn=chat, permission_mode="bypass", auto_remember=False,
                                        num_ctx=65536))
    assert len(calls) == 2
    event = next(event for event in events if event["type"] == "tool_call" and event["tool"] == "web_search")
    source_id = event["sources"][0]["id"]
    assert event["query_sources"] == [{"query": query, "source_ids": [source_id]} for query in queries]
    assert event["engine_warnings"] == [{"query": query, **WARNINGS[0]} for query in queries]
    assert any("WARNING:" in message["content"] for message in messages[1] if message.get("role") == "tool")
    journal = RunJournal.load(events[-1]["run_id"])
    persisted = [json.loads(line) for line in journal.events_path.read_text(encoding="utf-8").splitlines()]
    saved = next(item for item in persisted if item["type"] == "tool_call" and item["tool"] == "web_search")
    assert saved["query_sources"] == event["query_sources"]
    assert saved["engine_warnings"] == event["engine_warnings"]
    assert not list(tmp_path.iterdir())


def test_new_web_metadata_passes_existing_sse_secret_redaction():
    from app.application.code_agent.run_journal import sanitize_event

    secret = "token=fixture-canary"
    event = {"type": "tool_call", "engine_warnings": [{"engine": "Bing", "error": secret}],
             "query_sources": [{"query": secret, "source_ids": ["src_fixture"]}]}
    cleaned = sanitize_event(event)
    assert "fixture-canary" not in json.dumps(cleaned)
    assert cleaned["query_sources"][0]["source_ids"] == ["src_fixture"]
    assert cleaned["engine_warnings"][0]["engine"] == "Bing"
