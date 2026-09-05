from copy import deepcopy
from unittest.mock import patch

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.delivery_session import build_continuation_kwargs
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.run_journal import RunJournal, related_sources
from app.application.code_agent.tools import _web
from app.application.web_evidence.receipts import format_source, make_source, source_ids, valid_source


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
                        "ERROR: timeout" if url.endswith("bad") else "[fetched]\n\nRead text.")
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

    with patch.object(_web, "_fetch_one", return_value="[fetched]\n\nPerformance rose by 20% in this particular test."):
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
