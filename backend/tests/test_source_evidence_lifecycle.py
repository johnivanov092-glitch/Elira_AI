from copy import deepcopy
from unittest.mock import patch

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.delivery_session import build_continuation_kwargs
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.run_journal import RunJournal, related_sources
from app.application.code_agent.tools import _web
from app.application.web_evidence.receipts import format_source, make_source, source_ids, valid_source
from app.infrastructure.search.web_runtime import PageFetchResult


def _source(**kwargs):
    return make_source(
        run_id="original", tool="web_fetch", url="https://example.org/source",
        status="excerpt", quote="Performance rose by 20% in this particular test.",
        content_hash="a" * 64, offset=0, fetched_at=123.0, quote_verified=True,
        **kwargs,
    )


def test_excerpt_identity_and_presentation_do_not_certify_claim_meaning():
    source = _source()
    evidence = RunEvidence(sources=[source])
    assert not evidence.has_external_source
    # The marker alone (or a compressed summary) cannot prove excerpt delivery.
    marker = f"[[source:{source['id']}]]"
    evidence.mark_sources_presented([{"role": "assistant", "content": marker}])
    assert evidence.citations(marker)[0]["status"] == "unresolved"
    evidence.mark_sources_presented([{"role": "tool", "content": marker}])
    assert evidence.citations(marker)[0]["status"] == "unresolved"
    evidence.mark_sources_presented([{"role": "tool", "content": format_source(source)}])
    assert evidence.has_external_source
    citation = evidence.citations("All models run twice as fast. " + marker)[0]
    assert citation["status"] == "matched"
    assert citation["claim_support"] == "not_assessed"
    changed = deepcopy(source)
    changed["quote"] = "All models run twice as fast."
    assert not valid_source(changed)


def test_failed_page_and_passport_are_not_presented_excerpts(monkeypatch):
    monkeypatch.setattr(_web, "_fetch_one", lambda url, limit:
                        PageFetchResult(final_url=url, error="timeout") if url.endswith("bad")
                        else PageFetchResult(text="Read text.", final_url=url, status_code=200))
    result = _web.tool_web_fetch(urls=["https://example.org/good", "https://example.org/bad"])
    assert result["ok"] is True
    assert {source["status"] for source in result["sources"]} == {"excerpt", "failed"}
    assert next(source for source in result["sources"] if source["status"] == "failed")["quote"] == ""
    passport = make_source(run_id="original", tool="web_fetch", url="https://example.org/passport", status="fetched", doc_id="doc")
    evidence = RunEvidence(sources=[passport])
    evidence.mark_sources_presented([{"role": "tool", "content": format_source(passport)}])
    assert not evidence.has_external_source


def test_reference_examples_in_code_are_not_citations():
    assert source_ids('Use `[[source:example]]` syntax.\n```\n[[source:another]]\n```') == []


@pytest.mark.parametrize("field,value", [("status", []), ("url", 12), ("offset", True),
                                         ("fetched_at", float("inf")), ("quote_verified", "true")])
def test_malformed_persisted_source_is_rejected(field, value):
    source = _source()
    source[field] = value
    assert not valid_source(source)


def test_user_copied_marker_is_not_a_runtime_presentation_receipt():
    source = _source()
    evidence = RunEvidence(sources=[source])
    evidence.mark_sources_presented([{"role": "user", "content": format_source(source)}])
    assert not evidence.has_external_source
    assert source["quote"] in evidence.source_context(
        [{"role": "user", "content": format_source(source)}], missing_only=True,
    )
    assert len(evidence.source_context([], max_chars=500)) <= 500


def _first_run(tmp_path):
    calls = 0

    def chat(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"message": {"content": "", "tool_calls": [{"function": {
                "name": "web_fetch", "arguments": {"url": "https://example.org/source"},
            }}]}}
        markers = source_ids("\n".join(message.get("content", "") for message in kwargs["messages"]))
        assert markers
        from app.infrastructure.llm.openai_compatible import _normalize_messages_for_request
        wire_messages = _normalize_messages_for_request(kwargs["messages"])
        for index, message in enumerate(wire_messages):
            if message.get("tool_calls"):
                assert all(item["role"] == "tool" for item in wire_messages[index + 1:index + 1 + len(message["tool_calls"])])
        return {"message": {"content": "В этом тесте рост составил 20%. [[source:" + markers[-1] + "]]", "tool_calls": []}}

    with patch.object(_web, "_fetch_one", return_value=PageFetchResult(
        text="Performance rose by 20% in this particular test.", final_url="https://example.org/source", status_code=200,
    )):
        events = list(stream_code_agent(
            user_message="Прочитай источник и приведи результат теста", project_root=tmp_path,
            session_id="chat-a", run_id="first-evidence", chat_fn=chat,
            base_tools=["web_fetch"], auto_remember=False,
        ))
    final = next(event for event in events if event["type"] == "final_response")
    assert final["source_status"] == "matched"
    assert next(event for event in events if event["type"] == "tool_call")["sources"]
    return final


@pytest.mark.parametrize("resume", [False, True])
def test_source_snapshot_survives_journal_and_continuation(tmp_path, monkeypatch, resume):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    final = _first_run(tmp_path)
    source = final["citations"][0]["source"]
    state = RunJournal.load("first-evidence").state
    assert state["web_sources"][0]["excerpt_hash"] == source["excerpt_hash"]
    assert state["citations"][0]["status"] == "matched"

    def continued(**kwargs):
        context = "\n".join(message.get("content", "") for message in kwargs["messages"])
        assert source["quote"] in context
        assert f"[[source:{source['id']}]]" in context
        return {"message": {"content": "Это результат конкретного теста. " + f"[[source:{source['id']}]]", "tool_calls": []}}

    if resume:
        kwargs = build_continuation_kwargs("first-evidence", chat_fn=continued)
    else:
        kwargs = {
            "user_message": "Уточни условия теста", "project_root": tmp_path,
            "session_id": "chat-a", "run_id": "next-evidence", "chat_fn": continued,
            "conversation_history": [{"role": "assistant", "content": final["text"]}],
            "source_run_ids": ["first-evidence"], "auto_remember": False,
        }
    events = list(stream_code_agent(**kwargs))
    next_final = next(event for event in events if event["type"] == "final_response")
    assert next_final["source_status"] == "matched"
    assert next_final["citations"][0]["source"]["origin_run_id"] == "first-evidence"


def test_source_restoration_rejects_other_chat_and_client_forgery(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    final = _first_run(tmp_path)
    history = [{"role": "assistant", "content": final["text"]}]
    assert related_sources("chat-b", ["first-evidence"], history) == []
    assert related_sources("chat-a", ["../first-evidence"], history) == []
    assert related_sources("chat-a", ["first-evidence"], [{"content": "[[source:invented]]"}]) == []


def test_source_context_survives_real_compaction(tmp_path):
    from app.application.context.compaction import maybe_compact

    source = _source()
    evidence = RunEvidence(sources=[source])
    sources_message = {"role": "assistant", "content": evidence.source_context([]), "_msg_id": "web-source-context"}
    messages = [{"role": "system", "content": "Ты Elira."}, sources_message]
    messages.extend({"role": "user" if i % 2 == 0 else "assistant", "content": "Long history " * 200} for i in range(20))
    # Use the compactor directly; it must keep the exact source snapshot.
    packed, compacted = maybe_compact(
        messages, 4096, "test-model", None,
        lambda **kwargs: {"ok": True, "summary": "Старый разговор сжат."},
        pinned_message_ids={"web-source-context"},
    )
    assert compacted
    evidence.mark_sources_presented(packed)
    assert evidence.citations(f"[[source:{source['id']}]]")[0]["status"] == "matched"


def test_rebuild_after_compaction_keeps_used_excerpt_priority():
    from app.application.context.compaction import maybe_compact

    sources = [make_source(run_id="original", tool="web_fetch", url=f"https://example.org/{i}",
               status="excerpt", quote=f"Excerpt {i}: " + "x" * 1400, offset=0, quote_verified=True)
               for i in range(8)]
    evidence = RunEvidence(sources=sources)
    used_id = sources[0]["id"]
    messages = [{"role": "system", "content": "Elira"},
                {"role": "assistant", "content": f"Old answer [[source:{used_id}]]"}]
    messages.extend({"role": "user" if i % 2 else "assistant", "content": "Long history " * 250} for i in range(30))
    messages.insert(1, {"role": "assistant", "content": evidence.source_context(messages), "_msg_id": "web-source-context"})
    packed, compacted = maybe_compact(messages, 8192, "test-model", None,
        lambda **kwargs: {"ok": True, "summary": "Earlier source discussed."}, pinned_message_ids={"web-source-context"})
    assert compacted
    assert sources[0]["quote"] in evidence.source_context(packed)
    # The same priority survives a durable snapshot/Resume without the old text.
    resumed = RunEvidence(sources=evidence.sources)
    assert sources[0]["quote"] in resumed.source_context([])


def test_web_tool_history_keeps_exact_wire_prefix_without_duplicate_excerpts(tmp_path, monkeypatch):
    from app.infrastructure.llm.openai_compatible import _normalize_messages_for_request

    captures = []

    def chat(**kwargs):
        captures.append({"messages": deepcopy(kwargs["messages"]), "tools": deepcopy(kwargs["tools"])})
        index = len(captures)
        if index <= 3:
            return {"message": {"content": "", "tool_calls": [{
                "id": f"web-call-{index}", "function": {
                    "name": "web_fetch", "arguments": {"url": f"https://example.org/{index}"},
                },
            }]}}
        marker = source_ids(str(kwargs["messages"]))[-1]
        return {"message": {"content": f"Observed fixture fact. [[source:{marker}]]", "tool_calls": []}}

    monkeypatch.setattr(_web, "_fetch_one", lambda url, limit: PageFetchResult(
        text=f"Exact fact from {url}. " * 18, final_url=url, status_code=200,
    ))
    events = list(stream_code_agent(
        user_message="Read three fixture pages and compare their facts.", project_root=tmp_path,
        chat_fn=chat, model="test-model", num_ctx=131072, base_tools=["web_fetch"], auto_remember=False,
    ))
    assert len(captures) == 4
    assert not any(event["type"] == "context_compacted" for event in events)
    for previous, current in zip(captures, captures[1:]):
        before = _normalize_messages_for_request(previous["messages"])
        after = _normalize_messages_for_request(current["messages"])
        assert previous["tools"] == current["tools"]
        assert before == after[:len(before)]
    assert not any(message.get("_msg_id") == "web-source-context" for call in captures for message in call["messages"])
    final = next(event for event in events if event["type"] == "final_response")
    assert final["source_status"] == "matched"
    assert final["citations"][0]["claim_support"] == "not_assessed"


def test_loop_restores_exact_excerpts_missing_from_truncated_tool_output(tmp_path, monkeypatch):
    calls = []
    events = []

    def chat(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        if len(calls) == 1:
            return {"message": {"content": "", "tool_calls": [{"id": "long-web-call", "function": {
                "name": "web_fetch", "arguments": {"url": "https://example.org/long", "max_chars": 18000},
            }}]}}
        sources = next(event["sources"] for event in events if event["type"] == "tool_call")
        tool_text = "\n".join(message["content"] for message in kwargs["messages"] if message["role"] == "tool")
        restored_text = "\n".join(message["content"] for message in kwargs["messages"] if message.get("_msg_id") == "web-source-context")
        omitted = [source for source in sources if source["quote"] not in tool_text]
        restored = next(source for source in omitted if source["quote"] in restored_text)
        assert len(restored_text) <= 7000
        from app.infrastructure.llm.openai_compatible import _normalize_messages_for_request
        wire = _normalize_messages_for_request(kwargs["messages"])
        # A final assistant message is a completion prefix in local chat
        # templates. Source restoration must request a new answer, not prefill it.
        assert wire[-1]["role"] != "assistant"
        return {"message": {"content": f"Recovered exact excerpt. [[source:{restored['id']}]]", "tool_calls": []}}

    body = "".join((f"Section {index}. " + str(index) * 1500)[:1500] for index in range(12))
    monkeypatch.setattr(_web, "_fetch_one", lambda url, limit: PageFetchResult(text=body, final_url=url, status_code=200))
    for event in stream_code_agent(
        user_message="Read the fixture page and state its facts.", project_root=tmp_path,
        chat_fn=chat, model="test-model", num_ctx=131072, base_tools=["web_fetch"], auto_remember=False,
    ):
        events.append(event)
    assert len(calls) == 2
    assert next(event for event in events if event["type"] == "final_response")["source_status"] == "matched"


def test_loop_restores_used_excerpt_after_real_compaction(tmp_path, monkeypatch):
    from app.application.code_agent import agent_loop, loop_helpers

    captures = []
    events = []
    original_prepare = agent_loop._prepare_messages_for_llm

    def force_compaction(messages, **kwargs):
        if len(captures) == 3:
            kwargs["context_profile"] = {
                **kwargs["context_profile"],
                "compaction_thresholds": {"auto": {"percent": 0.001}, "strong": {"percent": 0.001}, "critical": {"percent": 100}},
            }
        return original_prepare(messages, **kwargs)

    monkeypatch.setattr(agent_loop, "_prepare_messages_for_llm", force_compaction)
    monkeypatch.setattr(loop_helpers, "summarize_history", lambda **kwargs: {"ok": True, "summary": "Earlier source discussed."})
    monkeypatch.setattr(_web, "_fetch_one", lambda url, limit: PageFetchResult(
        text=f"Fact from {url}. " * 40, final_url=url, status_code=200,
    ))

    def chat(**kwargs):
        captures.append(deepcopy(kwargs["messages"]))
        first_source = next((event["sources"][0] for event in events if event["type"] == "tool_call"), None)
        reference = f"[[source:{first_source['id']}]]" if first_source else ""
        if len(captures) <= 3:
            return {"message": {"content": reference, "tool_calls": [{"id": f"compact-call-{len(captures)}", "function": {
                "name": "web_fetch", "arguments": {"url": f"https://example.org/{len(captures)}"},
            }}]}}
        tool_text = "\n".join(message["content"] for message in kwargs["messages"] if message["role"] == "tool")
        restored_text = "\n".join(message["content"] for message in kwargs["messages"] if message.get("_msg_id") == "web-source-context")
        assert first_source["quote"] not in tool_text
        assert first_source["quote"] in restored_text
        return {"message": {"content": "Original exact fixture fact. " + reference, "tool_calls": []}}

    for event in stream_code_agent(
        user_message="Read three fixture pages and compare their facts.", project_root=tmp_path,
        chat_fn=chat, model="test-model", num_ctx=131072, base_tools=["web_fetch"], auto_remember=False,
    ):
        events.append(event)
    assert len(captures) == 4
    assert any(event["type"] == "context_compacted" for event in events)
    final = next(event for event in events if event["type"] == "final_response")
    assert final["source_status"] == "matched"
    assert final["citations"][0]["claim_support"] == "not_assessed"
