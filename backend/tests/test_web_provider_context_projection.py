"""Provider discovery projection keeps the retained history and read receipts intact."""
from __future__ import annotations

from copy import deepcopy
import re

import pytest

from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.agent_loop import request_cancel, stream_code_agent
from app.application.code_agent.tools import _web
from app.application.code_agent.turn_context import TurnContext
from app.application.web_evidence.receipts import excerpt_sources, format_source
from app.infrastructure.search.web_runtime import PageFetchResult


URL = "https://example.org/report"
BODY = "Measured 17 events in one laboratory sample."
SNIPPET = "Unconfirmed discovery statement."
SEARCH = "Search: sample\nPrimary report\n" + URL + "\nsource_id=discovery\n" + SNIPPET + "\nWARNING: timeout"


def _call(name, call_id):
    return {"id": call_id, "function": {"name": name, "arguments": {}}}


def _group(name, text, call_id):
    return [{"role": "assistant", "content": "", "tool_calls": [_call(name, call_id)]},
            {"role": "tool", "name": name, "tool_call_id": call_id, "content": text}]


def _setup(tmp_path, messages):
    source = excerpt_sources(run_id="projection", tool="web_fetch", url=URL, text=BODY, fetched_at=1.0)[0]
    evidence = RunEvidence(sources=[source])
    context = TurnContext(messages=messages, raw_user_message="Explain the measured sample.",
                          root=tmp_path, working_dir=None, run_id="projection")
    return context, evidence, source


def _project(context, evidence):
    return context.provider_messages(read_source_handles=evidence.read_source_handles,
        project_search_without_snippets=lambda text: text.replace(SNIPPET, "[snippet omitted]"))


def test_actual_read_projects_only_older_search_content_without_changing_history(tmp_path):
    context, evidence, source = _setup(tmp_path, _group("web_search", SEARCH, "search"))
    context.messages.extend(_group("web_fetch", format_source(source), "read"))
    context.messages.append({"role": "user", "content": "Trailing user note"})
    original = deepcopy(context.messages)
    provider = _project(context, evidence)
    assert context.messages == original
    assert provider[:1] == original[:1] and provider[2:] == original[2:]
    assert provider[1] == {**original[1], "content": SEARCH.replace(SNIPPET, "[snippet omitted]")}
    assert URL in provider[1]["content"] and "source_id=discovery" in provider[1]["content"]
    assert "WARNING: timeout" in provider[1]["content"]
    assert evidence.read_source_handles(provider) == (source["id"],)
    evidence.mark_sources_presented(provider)
    assert evidence.presented_sources[0]["quote"] == BODY


@pytest.mark.parametrize("read_text", ["Fetch failed: HTTP 403", "[[source:{id}]]", BODY])
def test_failure_passport_or_unbound_text_keeps_discovery(tmp_path, read_text):
    context, evidence, source = _setup(tmp_path, _group("web_search", SEARCH, "search"))
    context.messages.extend(_group("web_fetch", read_text.format(id=source["id"]), "read"))
    assert _project(context, evidence) is context.messages


@pytest.mark.parametrize("broken", ["missing", "wrong_id", "wrong_name", "interleaved"])
def test_incomplete_or_mismatched_tool_group_keeps_retained_input(tmp_path, broken):
    context, evidence, source = _setup(tmp_path, _group("web_search", SEARCH, "search"))
    group = _group("web_fetch", format_source(source), "read")
    if broken == "missing":
        group = group[:1]
    elif broken == "wrong_id":
        group[1]["tool_call_id"] = "unrelated"
    elif broken == "wrong_name":
        group[1]["name"] = "browser"
    else:
        group.insert(1, {"role": "user", "content": "Interleaved text"})
    context.messages.extend(group)
    assert _project(context, evidence) is context.messages


def test_existing_idless_results_match_declared_order_without_dropping_tool_pairs(tmp_path):
    context, evidence, source = _setup(tmp_path, _group("web_search", SEARCH, "search"))
    context.messages.extend(_group("web_fetch", format_source(source), "read"))
    for message in context.messages:
        message.pop("tool_call_id", None)
    provider = _project(context, evidence)
    assert SNIPPET not in provider[1]["content"]
    assert [row.get("name") for row in provider] == [row.get("name") for row in context.messages]
    assert [row.get("tool_calls") for row in provider] == [row.get("tool_calls") for row in context.messages]


def test_new_search_keeps_snippet_until_another_actual_read(tmp_path):
    context, evidence, source = _setup(tmp_path, _group("web_search", SEARCH, "first-search"))
    context.messages.extend(_group("web_fetch", format_source(source), "first-read"))
    context.messages.extend(_group("web_search", SEARCH, "new-search"))
    first = _project(context, evidence)
    assert SNIPPET not in first[1]["content"] and SNIPPET in first[5]["content"]
    context.messages.extend(_group("web_fetch", format_source(source), "next-read"))
    second = _project(context, evidence)
    assert SNIPPET not in second[1]["content"] and SNIPPET not in second[5]["content"]
    assert SNIPPET in context.messages[1]["content"] and SNIPPET in context.messages[5]["content"]


@pytest.mark.parametrize("read_first", [False, True])
def test_mixed_batch_search_remains_available_in_the_completed_read_group(tmp_path, read_first):
    context, evidence, source = _setup(tmp_path, _group("web_search", SEARCH, "old-search"))
    results = [_group("web_search", SEARCH, "batch-search"),
               _group("web_fetch", format_source(source), "batch-read")]
    if read_first:
        results.reverse()
    context.messages.append({"role": "assistant", "content": "", "tool_calls": [row[0]["tool_calls"][0]
                                                                                   for row in results]})
    context.messages.extend(row[1] for row in results)
    provider = _project(context, evidence)
    assert SNIPPET not in provider[1]["content"]
    assert any(SNIPPET in row["content"] for row in provider[3:] if row.get("name") == "web_search")


def test_restored_excerpt_does_not_hide_a_new_search_or_a_compacted_away_read(tmp_path):
    context, evidence, source = _setup(tmp_path, _group("web_search", SEARCH, "search"))
    context.messages.append({"role": "user", "content": format_source(source), "_msg_id": "web-source-context"})
    assert evidence.read_source_handles(context.messages) == (source["id"],)
    # The original read group was compacted away; restoration alone must not
    # move the read boundary past a newly issued discovery operation.
    assert _project(context, evidence) is context.messages
    context.messages[2:2] = _group("web_fetch", format_source(source), "read")
    context.messages[4:4] = _group("web_search", SEARCH, "new-search")
    provider = _project(context, evidence)
    assert SNIPPET not in provider[1]["content"] and SNIPPET in provider[5]["content"]
    assert provider[-1]["_msg_id"] == "web-source-context"


def test_projection_disabled_by_caller_preserves_work_context(tmp_path):
    context, evidence, source = _setup(tmp_path, _group("web_search", SEARCH, "search"))
    context.messages.extend(_group("web_fetch", format_source(source), "read"))
    assert context.provider_messages() is context.messages
    assert context.provider_messages(read_source_handles=evidence.read_source_handles) is context.messages


@pytest.mark.parametrize("work", [None, "mutation"])
def test_coordinator_projects_only_readonly_web_and_keeps_raw_results_and_tools(tmp_path, monkeypatch, work):
    discovery = [{"title": "Primary report", "url": URL, "content": SNIPPET + "\nSecond snippet line."}]
    monkeypatch.setattr("app.infrastructure.search.web_search.search_web",
                        lambda *args, **kwargs: {"ok": True, "sources": discovery})
    monkeypatch.setattr(_web, "_fetch_one", lambda url, limit: PageFetchResult(text=BODY, final_url=url))
    tools = {"web_search", "web_fetch", "write_file", "runtime_control"}
    run_id = "projection-" + str(work) + "-" + tmp_path.name
    calls = []
    steps = []
    if work == "mutation":
        steps.append(("write_file", {"path": "note.txt", "content": "Requested work started."}))
    steps.extend([("web_search", {"query": "primary report"}), ("web_fetch", {"url": URL})])

    def chat(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        assert {tool["function"]["name"] for tool in kwargs["tools"]} >= tools
        if len(calls) <= len(steps):
            name, arguments = steps[len(calls) - 1]
            return {"message": {"tool_calls": [{"id": str(len(calls)), "function": {
                "name": name, "arguments": arguments}}]}}
        search = next(row["content"] for row in kwargs["messages"] if row.get("name") == "web_search")
        read = next(row["content"] for row in kwargs["messages"] if row.get("name") == "web_fetch")
        assert BODY in read and URL in search and "Primary report" in search and "source_id=w_" in search
        if work is None:
            assert SNIPPET not in search and "Second snippet line." not in search
        else:
            assert SNIPPET in search and "Second snippet line." in search
            assert request_cancel(run_id)
            return {"message": {"content": ""}}
        marker = re.search(r"\[\[source:[^\]]+\]\]", read).group()
        return {"message": {"content": "В выборке зарегистрировали 17 событий. " + marker}}

    events = list(stream_code_agent(user_message="Прочитай отчёт и объясни результат.", project_root=tmp_path,
        chat_fn=chat, run_id=run_id, base_tools=sorted(tools), num_ctx=65536,
        permission_mode="bypass", auto_remember=False))
    assert len(calls) == len(steps) + 1
    raw_search = next(event for event in events if event["type"] == "tool_call" and event["tool"] == "web_search")
    assert SNIPPET in raw_search["result"] and "Second snippet line." in raw_search["result"]
    if work is not None:
        assert events[-1]["stop_reason"] == "cancelled"
        assert not any(event["type"] == "final_response" for event in events)
        return
    read_event = next(event for event in events if event["type"] == "tool_call" and event["tool"] == "web_fetch")
    final = next(event for event in events if event["type"] == "final_response")
    assert final["source_status"] == "matched"
    assert {source["source_id"] for source in final["citations"]} <= {source["id"] for source in read_event["sources"]}
