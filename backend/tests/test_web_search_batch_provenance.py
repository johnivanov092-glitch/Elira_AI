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
from app.application.code_agent.task_outcomes import TaskOutcome, task_decide
from app.application.code_agent.tools import build_tool_dispatch
from app.application.code_agent.tools._web import tool_web_search
from app.application.web_evidence.receipts import MAX_SOURCES, make_source


QUERIES = [
    "upcoming games October November December 2026 PC PS5",
    "лучшие игры 2026 осень зима PC PS5 релизы",
    "upcoming PS5 games 2026 highly anticipated",
]


def test_primary_query_is_retained_when_model_also_supplies_parallel_queries():
    with patch("app.application.code_agent.tools._web._run_search", return_value=[]) as search:
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

    with patch("app.infrastructure.search.web_search.search_web", side_effect=search):
        result = tool_web_search(queries=queries, categories="general", time_range="month", top_k=top_k)
    return result


def _record(result, *, queries=None, evidence=None, status="ok"):
    evidence = evidence if evidence is not None else RunEvidence()
    evidence.record_tool_result(
        tool_name="web_search", arguments={"queries": queries if queries is not None else QUERIES},
        execution_status=status, output=result, text_result=result["text"], state_changed=False,
    )
    return evidence


def _config(query, url):
    return {"disposition": "one_off", "reason": "Read-only search", "inputs": [], "targets": [],
            "delivery": {"mode": "none", "targets": []}, "requirements": [{
                "id": "search", "text": "Find the declared source for the declared query", "mandatory": True,
                "verification": {"checks": [{"kind": "web_search", "query": query, "url": url}]},
            }]}


def _verified(tmp_path, evidence, query, url):
    outcome = TaskOutcome()
    outcome.set_contract("Find release documentation", [])
    result = task_decide(tmp_path, _config(query, url))
    outcome.observe("runtime_control", {"operation": "task_decide"}, {"ok": True, "result": result},
                    project_root=tmp_path, input_epoch=0)
    receipt = outcome.verify_answer("Search completed.", evidence, 0)
    return receipt["checks"][0]["passed"] if receipt else False


def test_native_batch_emits_canonical_query_to_source_ids():
    result = _native_batch()
    ids = {source["url"]: source["id"] for source in result["sources"]}
    assert result["query_sources"] == [
        {"query": QUERIES[0], "source_ids": [ids[URL_A]]},
        {"query": QUERIES[1], "source_ids": [ids[URL_B]]},
        {"query": QUERIES[2], "source_ids": []},
    ]


def test_real_native_evidence_verifier_accepts_batch_member_without_cross_binding(tmp_path):
    evidence = _record(_native_batch())
    assert _verified(tmp_path, evidence, QUERIES[0], URL_A) is True
    assert _verified(tmp_path, evidence, QUERIES[1], URL_B) is True
    assert _verified(tmp_path, evidence, QUERIES[1], URL_A) is False
    assert _verified(tmp_path, evidence, QUERIES[2], URL_A) is False


def test_shared_url_and_repeated_query_retain_their_own_provenance(tmp_path):
    queries = [QUERIES[0], QUERIES[1], QUERIES[0]]
    result = _native_batch({QUERIES[0]: [URL_A, URL_A], QUERIES[1]: [URL_A, URL_B]}, queries=queries)
    evidence = _record(result, queries=queries)
    assert len(result["sources"]) == 2
    assert result["query_sources"][0] == result["query_sources"][2]
    assert len(result["query_sources"][0]["source_ids"]) == 1
    assert evidence.tool_operations[0]["query_count"] == 3
    assert _verified(tmp_path, evidence, QUERIES[0], URL_A)
    assert _verified(tmp_path, evidence, QUERIES[1], URL_A)
    assert _verified(tmp_path, evidence, QUERIES[1], URL_B)
    assert not _verified(tmp_path, evidence, QUERIES[0], URL_B)
    assert not _verified(tmp_path, evidence, QUERIES[0].upper(), URL_A)


def test_legacy_single_query_still_verifies_without_batch_metadata(tmp_path):
    with patch("app.infrastructure.search.web_search.search_web", return_value={
        "ok": True, "sources": [{"href": URL_A, "title": "Release source"}],
    }):
        result = tool_web_search(query=QUERIES[0])
    assert "query_sources" not in result
    evidence = _record(result, queries=[QUERIES[0]])
    assert evidence.web_operations == [{"tool_name": "web_search", "queries": [QUERIES[0]],
                                        "source_ids": [result["sources"][0]["id"]]}]
    assert _verified(tmp_path, evidence, QUERIES[0], URL_A)


def test_legacy_ambiguous_batch_cannot_borrow_the_flat_source_list(tmp_path):
    result = _native_batch()
    result.pop("query_sources")
    evidence = _record(result)
    assert evidence.sources
    assert evidence.web_operations == []
    assert not _verified(tmp_path, evidence, QUERIES[0], URL_A)
    assert not _verified(tmp_path, evidence, QUERIES[1], URL_A)


@pytest.mark.parametrize("tamper", [
    "null", "wrong_shape", "missing_row", "unknown_query", "wrong_order", "unknown_field",
    "null_ids", "nonstring_id", "foreign_id", "duplicate_id", "invalid_receipt", "wrong_tool", "wrong_status",
])
def test_invalid_batch_metadata_does_not_produce_successful_search_proof(tmp_path, tamper):
    result = _native_batch()
    rows = result["query_sources"]
    if tamper == "null":
        result["query_sources"] = None
    elif tamper == "wrong_shape":
        result["query_sources"] = {"query": QUERIES[0]}
    elif tamper == "missing_row":
        rows.pop()
    elif tamper == "unknown_query":
        rows[0]["query"] = "never executed"
    elif tamper == "wrong_order":
        rows.reverse()
    elif tamper == "unknown_field":
        rows[0]["url"] = URL_A
    elif tamper == "null_ids":
        rows[0]["source_ids"] = None
    elif tamper == "nonstring_id":
        rows[0]["source_ids"] = [{}]
    elif tamper == "foreign_id":
        rows[0]["source_ids"] = [make_source(
            run_id="another-run", tool="web_search", url=URL_A, status="discovered",
        )["id"]]
    elif tamper == "duplicate_id":
        rows[0]["source_ids"] *= 2
    elif tamper == "invalid_receipt":
        result["sources"][0]["url"] = URL_B
    else:
        source = make_source(run_id="another-tool", tool="web_fetch" if tamper == "wrong_tool" else "web_search",
                             url=URL_A, status="discovered" if tamper == "wrong_tool" else "fetched")
        result["sources"][0] = source
        rows[0]["source_ids"] = [source["id"]]
    evidence = _record(result)
    assert evidence.web_operations == []
    assert not _verified(tmp_path, evidence, QUERIES[0], URL_A)


def test_previously_observed_source_does_not_validate_a_different_call_binding(tmp_path):
    old = _native_batch({"previous": [URL_A]}, queries=["previous"])
    evidence = _record(old, queries=["previous"])
    result = _native_batch({QUERIES[0]: [URL_B], QUERIES[1]: [], QUERIES[2]: []})
    result["query_sources"][0]["source_ids"] = [old["sources"][0]["id"]]
    _record(result, evidence=evidence)
    assert len(evidence.web_operations) == 1
    assert not _verified(tmp_path, evidence, QUERIES[0], URL_A)


def test_operation_snapshots_cannot_mutate_query_bindings(tmp_path):
    evidence = _record(_native_batch())
    snapshot = evidence.web_operations
    snapshot[0]["query_source_ids"][QUERIES[0]].clear()
    snapshot[0]["query_source_ids"][QUERIES[1]] = snapshot[0]["source_ids"]
    snapshot[0]["queries"].clear()
    assert _verified(tmp_path, evidence, QUERIES[0], URL_A)
    assert not _verified(tmp_path, evidence, QUERIES[1], URL_A)


@pytest.mark.parametrize("failure", ["partial", "provider_failed", "execution_failed"])
def test_existing_failed_or_partial_search_policy_stays_fail_closed(tmp_path, failure):
    result = _native_batch({QUERIES[0]: [URL_A], QUERIES[1]: RuntimeError("Unavailable"), QUERIES[2]: []}) \
        if failure == "partial" else _native_batch()
    if failure == "provider_failed":
        result["ok"] = False
    evidence = _record(result, status="error" if failure == "execution_failed" else "ok")
    assert evidence.web_operations == []
    assert not _verified(tmp_path, evidence, QUERIES[0], URL_A)


def test_healthy_empty_queries_are_observed_but_do_not_claim_discovery(tmp_path):
    result = _native_batch({query: [] for query in QUERIES})
    assert result["ok"] is True
    assert result["query_sources"] == [{"query": query, "source_ids": []} for query in QUERIES]
    evidence = _record(result)
    assert evidence.tool_operations[0]["query_count"] == 3
    assert evidence.web_operations == []
    assert not _verified(tmp_path, evidence, QUERIES[0], URL_A)


def test_receipt_registry_bound_does_not_reject_retained_sources_across_batches(tmp_path):
    # 5 queries x 10 results per call: 21 calls exceed the 1024-source registry.
    batches = [[f"batch-{batch}-query-{index}" for index in range(5)] for batch in range(21)]
    rows = {query: [f"https://example.org/{query}/{rank}" for rank in range(10)]
            for queries in batches for query in queries}
    results = [_native_batch(rows, queries=queries, top_k=10) for queries in batches]
    assert all(len(result["sources"]) == 50 for result in results)
    assert sum(len(result["sources"]) for result in results) > MAX_SOURCES
    evidence = None
    for result, queries in zip(results, batches):
        evidence = _record(result, queries=queries, evidence=evidence)
    assert len(evidence.sources) == MAX_SOURCES
    first_queries, last_queries = batches[0], batches[-1]
    for query in (last_queries[0], last_queries[2], last_queries[-1]):
        for rank in (0, 5, 9):
            assert _verified(tmp_path, evidence, query, rows[query][rank])
    assert _verified(tmp_path, evidence, first_queries[-1], rows[first_queries[-1]][0])
    assert not _verified(tmp_path, evidence, first_queries[0], rows[first_queries[0]][0])
    assert not _verified(tmp_path, evidence, last_queries[0], rows[last_queries[-1]][0])


@pytest.mark.parametrize("query,accepted", [(QUERIES[0], True), (QUERIES[1], False)])
def test_canonical_executor_observations_and_completion_use_actual_native_batch(tmp_path, query, accepted):
    observations = RunObservations(task_spec=None, durable_state={}, resume=False)
    observations.outcome.set_contract("Find release documentation", [])
    dispatch = build_tool_dispatch(tmp_path)

    def execute(name, args):
        result = execute_tool(ToolExecutionRequest(
            run_id="batch-completion", agent_id="code-agent", project_scope_id="test",
            tool_name=name, args=args, source="code_agent", permission_mode="bypass",
        ), dispatch_fn=lambda tool, values: dispatch[tool](**values))
        observations.observe_result(name=name, args=args, output=result.output, status=result.status,
                                    text=result.output["text"], state_changed=False, root=tmp_path, bom_selected=False)
        observations.complete_result(name=name, args=args, output=result.output, status=result.status,
                                     text=result.output["text"], ok=result.output["ok"],
                                     state_changed=False, verification="")
        return result

    def search(value, **kwargs):
        return {"ok": True, "sources": [{"href": URL_A if value == QUERIES[0] else URL_B,
                                         "title": "Release source"}] if value != QUERIES[2] else []}

    with patch("app.infrastructure.search.web_search.search_web", side_effect=search):
        result = execute("web_search", {"queries": QUERIES, "top_k": 10})
    assert result.status == "ok"
    assert result.output["query_sources"][0]["source_ids"]
    execute("runtime_control", {"operation": "task_decide", "config": _config(query, URL_A)})
    decision = AnswerAcceptance().evaluate(
        final_text="Search completed.", raw_user_message="Find release documentation", pending_redirected_jobs=[],
        active_capability_groups=["web", "runtime"], task_outcome=observations.outcome,
        run_evidence=observations.evidence, code_input_epoch=0, quote_word_limit=None,
        persistence_policy={"rag": False, "direct_memory": False, "learning": False},
        step=1, run_id="batch-completion", criteria_rows=[],
    )
    assert (decision.action == "accept" and decision.answer_status == "complete") is accepted
    if not accepted:
        assert decision.action == "retry" and decision.reason == "outcome"
    assert not list(tmp_path.iterdir())
