from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.tools import _web
from app.application.tool_providers import BuiltinToolProvider
from app.application.web_evidence import corpus, retrieval
from app.infrastructure.web_corpus import store


def _provider(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_DB_PATH_OVERRIDE", str(tmp_path / "corpus.sqlite3"))
    monkeypatch.setattr(_web, "_web_corpus_on", lambda: True)
    monkeypatch.setattr(_web, "_current_run_id", lambda: "query-contract")
    monkeypatch.setattr(retrieval, "_embed_rerank", lambda *args: None)
    return BuiltinToolProvider(tmp_path, tool_names=["web_query"])


def _record(output):
    evidence = RunEvidence()
    evidence.record_tool_result(
        tool_name="web_query", arguments={"query": "violet telescope"},
        execution_status="ok", output=output, text_result=output["text"],
        state_changed=False,
    )
    return evidence


def test_web_query_preserves_verifiable_excerpts_through_provider(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch)
    monkeypatch.setattr(corpus, "_fetch_raw", lambda url: {
        "ok": True, "final_url": url, "mime": "text/plain", "charset": "utf-8",
        "content": b"The violet telescope is a unique retrieval marker.",
        "last_modified": None,
    })
    passport = corpus.ingest("https://example.org/source", "query-contract")
    assert passport["ok"] is True

    output = provider.dispatch("web_query", {"query": "violet telescope"})

    assert output["ok"] is True
    assert output.get("results"), "adapter discarded structured excerpts"
    hit = output["results"][0]
    assert hit["url"] == "https://example.org/source"
    assert hit["quote"] in output["text"]
    assert output["ranker"] == "bm25"
    assert retrieval.verify_quote(
        "query-contract", hit["doc_id"], hit["quote"], hit["offset"],
    )["quote_verified"] is True
    evidence = _record(output)
    assert not evidence.has_external_source
    evidence.mark_sources_presented([{"role": "tool", "content": output["text"]}])
    assert evidence.has_external_source


def test_empty_web_query_is_not_success_or_external_evidence(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch)

    output = provider.dispatch("web_query", {"query": "violet telescope"})

    assert not _record(output).has_external_source
    assert output["ok"] is False
    assert output["error"] == "no_results"
    assert output["results"] == []
    assert "web_fetch" in output["text"]
