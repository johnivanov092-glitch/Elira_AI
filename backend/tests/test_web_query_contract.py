from app.application.code_agent.run_evidence import RunEvidence
from webskill.application.code_agent.tools import _web
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from webskill.application.web_evidence import corpus, retrieval
from webskill.infrastructure.web_corpus import store


def _provider(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_DB_PATH_OVERRIDE", str(tmp_path / "corpus.sqlite3"))
    monkeypatch.setattr(_web, "_web_corpus_on", lambda: True)
    monkeypatch.setattr(_web, "_current_run_id", lambda: "query-contract")
    monkeypatch.setattr(retrieval, "_embed_rerank", lambda *args: None)
    path = Path(__file__).resolve().parents[2] / "skills/web-research/web.py"
    spec = importlib.util.spec_from_file_location("web_query_skill_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    def dispatch(name, arguments):
        assert name == "web_query"
        argv = ["query", "--query", arguments["query"]]
        return cli.execute(cli.parser().parse_args(argv))

    return SimpleNamespace(dispatch=dispatch)


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
    assert hit["bm25_score"] > 0
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


def test_unmatched_web_query_does_not_publish_unrelated_corpus_excerpt(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch)
    monkeypatch.setattr(corpus, "_fetch_raw", lambda url: {
        "ok": True, "final_url": url, "mime": "text/plain", "charset": "utf-8",
        "content": b"The violet telescope is a unique retrieval marker.",
        "last_modified": None,
    })
    assert corpus.ingest("https://example.org/source", "query-contract")["ok"]

    output = provider.dispatch("web_query", {"query": "cardiomyopathy influenza"})

    assert output["ok"] is False
    assert output["error"] == "no_results"
    assert output["results"] == []
    assert output.get("sources", []) == []
    assert "violet telescope" not in output["text"]
    evidence = _record(output)
    evidence.mark_sources_presented([{"role": "tool", "content": output["text"]}])
    assert not evidence.has_external_source


def test_web_query_presents_source_dates_separately_from_fetch_time(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch)
    monkeypatch.setattr(corpus, "_fetch_raw", lambda url: {
        "ok": True, "final_url": url, "mime": "text/html", "charset": "utf-8",
        "content": b"""<html><head><title>Release notes</title>
            <meta property="article:published_time" content="2025-02-14T12:00:00Z">
            <meta property="article:modified_time" content="2025-03-01T10:30:00Z">
            </head><body><p>The violet telescope has been released.</p></body></html>""",
        "last_modified": "Sat, 15 Mar 2025 00:00:00 GMT",
    })

    passport = corpus.ingest("https://www.python.org/release-notes", "query-contract")

    assert passport["dates"] == {"published": "2025-02-14", "modified": "2025-03-01"}
    assert passport["tier"] == "official"
    output = provider.dispatch("web_query", {"query": "violet telescope"})
    assert output["ok"] is True
    assert output["results"][0]["dates"] == passport["dates"]
    assert output["results"][0]["tier"] == "official"
    source = output["sources"][0]
    assert source["dates"] == passport["dates"]
    assert source["tier"] == "official"
    assert isinstance(source["fetched_at"], (int, float))
    assert "Опубликовано: 2025-02-14" in output["text"]
    assert "Изменено: 2025-03-01" in output["text"]
    assert "2025-03-15" not in output["text"]
