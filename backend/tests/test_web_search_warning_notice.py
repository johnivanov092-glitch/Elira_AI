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
from webskill.application.code_agent.tools._web import tool_web_search
from webskill.infrastructure.search.web_search import search_web
from webskill.core import web_engines
from test_web_search_engine_warnings import URL, WARNINGS, _http, _payload




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
