"""Provider outages remain visible at the native search tool boundary."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from webskill.application.code_agent.tools._web import tool_web_search


@pytest.mark.parametrize("args", [{"query": "docs"}, {"queries": ["docs", "manual"]}])
@pytest.mark.parametrize("failure", [
    {"ok": False, "error": "SEARXNG_URL is not configured", "sources": []},
    RuntimeError("SearXNG HTTP 503"),
])
def test_provider_failure_is_an_error_with_its_reason(args, failure):
    kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
    with patch("webskill.infrastructure.search.web_search.search_web", **kwargs):
        result = tool_web_search(**args)
    assert result["ok"] is False
    reason = str(failure) if isinstance(failure, Exception) else failure["error"]
    assert reason in result["text"]
    assert "No web results" not in result["text"]


def test_partial_batch_retains_sources_and_reports_failed_query():
    def search(query, **kwargs):
        if query == "failed":
            raise RuntimeError("SearXNG HTTP 503")
        return {"ok": True, "sources": [{"href": "https://example.com/docs", "title": "Docs"}]}

    with patch("webskill.infrastructure.search.web_search.search_web", side_effect=search):
        result = tool_web_search(queries=["healthy", "failed"])
    assert result["ok"] is True
    assert result["partial"] is True
    assert result["query_errors"] == [{"query": "failed", "error": "SearXNG HTTP 503"}]
    assert "WARNING: incomplete search" in result["text"]
    assert "SearXNG HTTP 503" in result["text"]
    assert result["sources"][0]["url"] == "https://example.com/docs"


def test_empty_batch_with_failed_query_is_incomplete_not_no_hits():
    def search(query, **kwargs):
        if query == "failed":
            raise RuntimeError("SearXNG timeout")
        return {"ok": True, "sources": []}

    with patch("webskill.infrastructure.search.web_search.search_web", side_effect=search):
        result = tool_web_search(queries=["empty", "failed"])
    assert result["ok"] is False
    assert "SearXNG timeout" in result["text"]
    assert result["query_errors"][0]["query"] == "failed"


@pytest.mark.parametrize("args", [{"query": "empty"}, {"queries": ["empty", "also empty"]}])
def test_healthy_empty_response_remains_valid(args):
    with patch("webskill.infrastructure.search.web_search.search_web",
               return_value={"ok": True, "sources": []}):
        result = tool_web_search(**args)
    assert result["ok"] is True
    assert "No web results" in result["text"]
    assert not result.get("query_errors")
