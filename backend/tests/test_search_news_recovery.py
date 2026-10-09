import pytest

from webskill.application.code_agent.tools._web import tool_web_search
from test_web_search_engine_warnings import _http


@pytest.mark.parametrize("sparse", [True, False])
def test_news_category_recovers_through_same_searxng_without_widening_period(sparse):
    def reply(params):
        if params["categories"] == "news":
            return {"results": [{"url": "https://news.example.org/old", "title": "A result"}] if sparse else [],
                    "unresponsive_engines": [] if sparse else [["bing news", "parsing error"]]}
        return {"results": [{"url": "https://science.example.org/current", "title": "Current report"}],
                "unresponsive_engines": []}

    with _http(reply=reply) as calls:
        output = tool_web_search(query="research last week", categories="news", time_range="week")
    assert output["ok"]
    assert [call["params"]["categories"] for call in calls] == ["news", "general"]
    assert all(call["params"]["time_range"] == "week" for call in calls)
    assert "https://science.example.org/current" in output["text"]
    assert "категория: general" in output["text"]
    if not sparse:
        assert output["engine_warnings"] == [{"engine": "bing news", "error": "parsing error"}]


def test_news_recovery_cannot_recurse_when_both_categories_fail():
    with _http({"results": [], "unresponsive_engines": [["engine", "timeout"]]}) as calls:
        output = tool_web_search(query="research", categories="news")
    assert len(calls) == 2
    assert not output["ok"] and output["engine_warnings"]
