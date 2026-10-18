"""Large native web batches retain receipts without bypassing context guards."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.loop_helpers import (
    ContextBudgetError, _prepare_messages_for_llm,
)
from app.application.code_agent.run_evidence import RunEvidence
from webskill.application.code_agent.tools._web import tool_web_fetch, tool_web_search
from app.application.context.profile import resolve_context_window
from app.application.context.usage import get_context_usage
from webskill.application.web_evidence.receipts import valid_source
from webskill.infrastructure.search.web_runtime import PageFetchResult


QUERIES = [f"documentation-query-{index}" for index in range(5)]
URLS = [f"https://example.org/documentation/{index}" for index in range(5)]


def test_native_thirty_by_thirty_retains_every_current_query_receipt():
    def search(query, **kwargs):
        assert kwargs["max_results"] == 10
        return {"ok": True, "sources": [
            {"href": f"https://example.org/{query}/{rank}",
             "title": f"Documentation {rank}", "body": "Read the documentation."}
            for rank in range(10)
        ]}

    with patch("webskill.infrastructure.search.web_search.search_web", side_effect=search):
        result = tool_web_search(queries=QUERIES, top_k=10)
    evidence = RunEvidence()
    evidence.record_tool_result(
        tool_name="web_search", arguments={"queries": QUERIES, "top_k": 10},
        execution_status="ok", output=result, text_result=result["text"], state_changed=False,
    )
    assert len(result["sources"]) == len(evidence.sources) == 50
    assert all(valid_source(source) for source in evidence.sources)
    retained = {source["id"]: source for source in evidence.sources}
    binding = evidence.web_operations[0]["query_source_ids"]
    for query in (QUERIES[0], QUERIES[2], QUERIES[-1]):
        assert len(binding[query]) == 10
        for rank in (0, 5, 9):
            url = f"https://example.org/{query}/{rank}"
            assert any(retained[source_id]["url"] == url for source_id in binding[query])

    def fetch(url, *, max_chars):
        return PageFetchResult(text="Configuration documentation. " * 100,
                               final_url=url, status_code=200)

    with patch("webskill.infrastructure.search.web_search.fetch_page", side_effect=fetch):
        read = tool_web_fetch(urls=URLS)
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"urls": URLS}, execution_status="ok", output=read,
        text_result=read["text"], state_changed=False,
    )
    evidence.mark_sources_presented([{"role": "tool", "name": "web_fetch", "content": read["text"]}])
    retained = {source["id"]: source for source in evidence.sources}
    assert len(retained) == 50 + len(read["sources"]) <= 1024
    assert {URLS[0], URLS[2], URLS[-1]} <= {source["url"] for source in evidence.presented_sources}
    for query in (QUERIES[0], QUERIES[2], QUERIES[-1]):
        assert all(source_id in retained for source_id in binding[query])


def _native_fetch_run(tmp_path, num_ctx, *, base_tools=None, task_instructions=""):
    calls, pages, outputs = [], {}, []
    responses = iter([
        {"message": {"content": "", "tool_calls": [{"function": {
            "name": "web_fetch", "arguments": {"urls": URLS, "max_chars": 50000},
        }}]}},
        {"message": {"content": "Прочитаны доступные выдержки документации.", "tool_calls": []}},
    ])

    def fetch(url, *, max_chars):
        assert max_chars == 50000
        index = URLS.index(url)
        body = f"PAGE_{index:02d}_START\n" + "Documentation details. " * 4000
        pages[url] = body
        return PageFetchResult(text=body[:max_chars], final_url=url, status_code=200,
                               truncated=len(body) > max_chars,
                               available_fragments=("configuration", "security"))

    def chat(**kwargs):
        if "tools" not in kwargs:
            return {"message": {"content": "Earlier documents summarized."}}
        calls.append({"messages": deepcopy(kwargs["messages"])})
        return next(responses)

    def native_fetch(**kwargs):
        output = tool_web_fetch(**kwargs)
        outputs.append(output)
        return output

    with patch("webskill.infrastructure.search.web_search.fetch_page", side_effect=fetch), patch(
        "app.application.code_agent.tools._dispatch.tool_web_fetch", side_effect=native_fetch,
    ):
        events = list(stream_code_agent(
            user_message="Прочитай доступные выдержки документации.", project_root=tmp_path,
            chat_fn=chat, permission_mode="bypass", auto_remember=False, num_ctx=num_ctx,
            base_tools=base_tools, task_instructions=task_instructions,
        ))
    return calls, events, pages, outputs


def test_native_thirty_pages_reach_next_canonical_model_turn(tmp_path):
    calls, events, original_pages, outputs = _native_fetch_run(tmp_path, 131072)
    assert len(calls) == 2
    content = next(message["content"] for message in calls[1]["messages"]
                   if message.get("role") == "tool" and message.get("name") == "web_fetch")
    assert 7000 <= len(content) <= 12000
    for index, url in enumerate(URLS):
        assert f"PAGE_{index:02d}_START" in content
        assert url in content
    assert len(outputs) == 1
    output = outputs[0]
    assert len(output["pages"]) == 5
    assert all(page["truncated"] is True for page in output["pages"])
    assert all(page["available_fragments"] == ["configuration", "security"] for page in output["pages"])
    receipts = output["sources"]
    assert receipts and all(valid_source(source) for source in receipts)
    for source in receipts:
        assert source["quote"] in original_pages[source["url"]]
        assert source["quote"] in content
        assert f"[[source:{source['id']}]]" in content
    assert {source["url"] for source in receipts} == set(URLS)
    assert events[-1]["type"] == "done" and events[-1]["ok"] is True


@pytest.mark.parametrize("num_ctx", [32768, 8192])
def test_small_windows_compact_history_without_oversized_model_request(num_ctx):
    profile = resolve_context_window(num_ctx, live=False)
    calls, audit = [], []
    history = [{"role": "system", "content": "Keep the user request."},
               {"role": "user", "content": "Read the documentation.", "_msg_id": "request"}]
    history.extend({"role": "user" if index % 2 == 0 else "assistant",
                    "content": "old documentation " * 1000} for index in range(40))
    history.extend([{"role": "assistant", "content": "Recent read."},
                    {"role": "tool", "name": "web_fetch", "content": "Current excerpt. " * 30}])

    def chat(**kwargs):
        calls.append(deepcopy(kwargs))
        return {"message": {"content": "Earlier documents summarized."}}

    packed, compacted, usage = _prepare_messages_for_llm(
        history, num_ctx=num_ctx, model="offline-model", chat_fn=chat, context_profile=profile,
        pinned_message_ids={"request"}, audit_sink=audit.append,
    )
    assert compacted and audit
    assert any(message.get("_msg_id") == "request" for message in packed)
    assert any("Current excerpt." in str(message.get("content")) for message in packed)
    assert usage["current_tokens"] <= profile["safe_input_budget"]
    assert usage["percent"] < profile["compaction_thresholds"]["critical"]["percent"]
    for call in calls:
        assert get_context_usage(call["messages"], ctx_size=num_ctx)["current_tokens"] < num_ctx


def test_native_large_batch_is_packed_for_small_window_before_model_call(tmp_path):
    num_ctx = 32768
    calls, events, _, outputs = _native_fetch_run(tmp_path, num_ctx, base_tools=("web_fetch",))
    assert len(calls) == 2
    assert events[-1]["type"] == "done" and events[-1]["ok"] is True
    packed = calls[1]["messages"]
    text = "\n".join(str(message.get("content") or "") for message in packed
                     if message.get("role") == "tool" or message.get("_msg_id") == "web-source-context")
    shown = [source for source in outputs[0]["sources"] if f"[[source:{source['id']}]]" in text]
    assert shown
    assert all(source["quote"] in text for source in shown)
    assert {URLS[0], URLS[2], URLS[-1]} <= {source["url"] for source in shown}
    # Within the 12000-character per-call budget the batch already fits 32K without
    # packing; packing is covered by the 8K-window tests below.
    prepared = [event["context"] for event in events if event.get("type") == "context_prepared"]
    assert prepared and all(usage["current_tokens"] < num_ctx for usage in prepared)


def test_full_canonical_eight_k_window_rejects_oversized_fixed_prefix_before_model(tmp_path):
    calls, events, _, outputs = _native_fetch_run(tmp_path, 8192, base_tools=("web_fetch",),
        task_instructions="Required project instruction. " * 4000)
    assert calls == [] and outputs == []
    assert events[-1]["type"] == "done" and events[-1]["ok"] is False
    assert events[-1]["error_code"] == "context_budget_exceeded"


def test_readonly_web_guidance_fits_eight_k_without_code_work_contract(tmp_path):
    calls, events, _, outputs = _native_fetch_run(tmp_path, 8192, base_tools=("web_fetch",))
    assert len(calls) == 2 and outputs
    assert events[-1]["type"] == "done" and events[-1]["ok"] is True


def test_eight_k_window_packs_native_receipts_when_fixed_prefix_fits():
    from webskill.application.code_agent.tools._web import tool_web_fetch

    def fetch(url, *, max_chars):
        index = URLS.index(url)
        return PageFetchResult(text=f"PAGE_{index:02d}_START\n" + "Configuration details. " * 4000,
                               final_url=url, status_code=200, truncated=True)

    with patch("webskill.infrastructure.search.web_search.fetch_page", side_effect=fetch):
        output = tool_web_fetch(urls=URLS, max_chars=50000)
    evidence = RunEvidence()
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"urls": URLS}, execution_status="ok", output=output,
        text_result=output["text"], state_changed=False,
    )
    profile = resolve_context_window(8192, live=False)
    messages = [{"role": "system", "content": "Read available excerpts; keep their source markers."},
                {"role": "user", "content": "Read documentation.", "_msg_id": "request"},
                {"role": "assistant", "content": "Reading."},
                {"role": "tool", "name": "web_fetch", "content": output["text"]}]
    packed, compacted, usage = _prepare_messages_for_llm(
        messages, num_ctx=8192, model="offline-model", context_profile=profile,
        chat_fn=lambda **kwargs: {"message": {"content": "Earlier documents summarized."}},
        restore_messages=evidence.restore_source_context, pinned_message_ids={"request"},
    )
    evidence.mark_sources_presented(packed)
    assert compacted
    assert usage["current_tokens"] <= profile["safe_input_budget"]
    assert usage["percent"] < profile["compaction_thresholds"]["critical"]["percent"]
    assert {URLS[0], URLS[2], URLS[-1]} <= {source["url"] for source in evidence.presented_sources}
    text = "\n".join(str(message.get("content") or "") for message in packed)
    shown = [source for source in output["sources"] if f"[[source:{source['id']}]]" in text]
    assert shown and all(source["quote"] in text for source in shown)
    assert {source["id"] for source in shown} == {source["id"] for source in evidence.presented_sources}


def test_small_window_search_packing_preserves_middle_provider_warning():
    profile = resolve_context_window(8192, live=False)
    warning = "WARNING: upstream engine unavailable; partial search results."
    original = "Search results:\n" + "related result\n" * 3000 + warning + "\n" + "related result\n" * 3000
    messages = [{"role": "system", "content": "Read results and disclose provider errors."},
                {"role": "user", "content": "Current request.", "_msg_id": "request"},
                {"role": "assistant", "content": "Searching."},
                {"role": "tool", "name": "web_search", "content": original}]
    packed, _, usage = _prepare_messages_for_llm(
        messages, num_ctx=8192, model="offline-model", context_profile=profile,
        chat_fn=lambda **kwargs: {"message": {"content": "Earlier results summarized."}},
        pinned_message_ids={"request"},
    )
    content = next(message["content"] for message in packed if message.get("role") == "tool")
    assert warning in content and "truncated" in content
    assert usage["current_tokens"] <= profile["safe_input_budget"]
    assert messages[-1]["content"] == original


@pytest.mark.parametrize("num_ctx", [32768, 8192])
def test_oversized_protected_request_cannot_reach_a_small_window_model(num_ctx):
    profile = resolve_context_window(num_ctx, live=False)
    model_calls = []
    messages = [{"role": "system", "content": "Mandatory instructions. " * num_ctx},
                {"role": "user", "content": "Current request.", "_msg_id": "request"},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "web_fetch"}}]},
                {"role": "tool", "name": "web_fetch", "content": "page data " * 12000}]
    with pytest.raises(ContextBudgetError):
        _prepare_messages_for_llm(
            messages, num_ctx=num_ctx, model="offline-model", context_profile=profile,
            chat_fn=lambda **kwargs: model_calls.append(kwargs), pinned_message_ids={"request"},
        )
    assert model_calls == []
