"""Query discovery proof must survive the native tool-to-completion boundary.

Only the external search facade is stubbed; native receipts, executor,
observations and answer verification execute their production implementations.
"""
from unittest.mock import patch

import pytest

from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool
from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.run_observations import RunObservations
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.code_agent.tools import build_tool_dispatch
from webskill.application.code_agent.tools._web import tool_web_search
from webskill.application.web_evidence.receipts import MAX_SOURCES, make_source


QUERIES = [
    "upcoming games October November December 2026 PC PS5",
    "лучшие игры 2026 осень зима PC PS5 релизы",
    "upcoming PS5 games 2026 highly anticipated",
]


def test_primary_query_is_retained_when_model_also_supplies_parallel_queries():
    with patch("webskill.application.code_agent.tools._web._run_search", return_value=[]) as search:
        result = tool_web_search(query="primary source", queries=["secondary source", "primary source"])
    assert search.call_count == 2
    assert {call.args[0] for call in search.call_args_list} == {"primary source", "secondary source"}
    assert [item["query"] for item in result["query_sources"]] == ["primary source", "secondary source"]
URL_A = "https://example.org/october"
URL_B = "https://example.org/november"


def _native_batch(rows=None, *, queries=None, top_k=10):
    rows = rows if rows is not None else {QUERIES[0]: [URL_A], QUERIES[1]: [URL_B], QUERIES[2]: []}
    queries = queries if queries is not None else QUERIES

    def search(query, **kwargs):
        value = rows[query]
        if isinstance(value, Exception):
            raise value
        return {"ok": True, "sources": [{"href": url, "title": "Release source", "body": "Release details"}
                                        for url in value]}

    with patch("webskill.infrastructure.search.web_search.search_web", side_effect=search):
        result = tool_web_search(queries=queries, categories="general", time_range="month", top_k=top_k)
    return result


def _record(result, *, queries=None, evidence=None, status="ok"):
    evidence = evidence if evidence is not None else RunEvidence()
    evidence.record_tool_result(
        tool_name="web_search", arguments={"queries": queries if queries is not None else QUERIES},
        execution_status=status, output=result, text_result=result["text"], state_changed=False,
    )
    return evidence


def test_native_batch_emits_canonical_query_to_source_ids():
    result = _native_batch()
    ids = {source["url"]: source["id"] for source in result["sources"]}
    assert result["query_sources"] == [
        {"query": QUERIES[0], "source_ids": [ids[URL_A]]},
        {"query": QUERIES[1], "source_ids": [ids[URL_B]]},
        {"query": QUERIES[2], "source_ids": []},
    ]


