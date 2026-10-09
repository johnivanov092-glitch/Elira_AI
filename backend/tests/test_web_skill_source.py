"""Skill extraction -> independently checked web bytes -> durable citable text."""
import hashlib
import json
import sqlite3

import pytest

from webskill.application.code_agent.tools import _web
from webskill.application.web_evidence import corpus, receipts, retrieval
from webskill.infrastructure.web_corpus import store


@pytest.fixture
def document(tmp_path, monkeypatch):
    url = "https://example.org/source.pdf"
    original = tmp_path / "source.pdf"
    original.write_bytes(b"%PDF original bytes")
    payload = {"ok": True, "complete": True, "text": "Dummy PDF file",
               "source": {"requested_url": url, "final_url": url,
                          "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                          "local_path": str(original)}}
    extraction = tmp_path / "extraction.json"
    extraction.write_text(json.dumps(payload), encoding="utf-8")
    raw = {"ok": True, "final_url": url, "mime": "application/pdf", "content": original.read_bytes()}
    monkeypatch.setattr(corpus, "_fetch_raw", lambda _: dict(raw))
    monkeypatch.setattr(_web, "_current_run_id", lambda: "skill-source")
    monkeypatch.setattr("webskill.context.run_persistence_policy", lambda _: {"rag": True})
    return url, original, extraction, payload, raw


def fetch(document, **kwargs):
    url, original, extraction, _, _ = document
    return _web.tool_web_fetch(project_root=original.parent, url=url, extraction_path=str(extraction), **kwargs)


def test_skill_source_quote_roundtrip_and_persisted_provenance(document):
    result = fetch(document)
    assert result["ok"], result
    source = result["sources"][0]
    assert receipts.valid_source(source)
    assert source["quote"] == "Dummy PDF file"
    assert source["provenance"]["raw_sha256"] == document[3]["source"]["sha256"]
    assert source["provenance"]["extraction_sha256"] == hashlib.sha256(document[2].read_bytes()).hexdigest()
    assert retrieval.verify_quote("skill-source", result["doc_id"], source["quote"], source["offset"])["quote_verified"]
    query = _web.tool_web_query(query="Dummy", doc_id=result["doc_id"])
    assert query["ok"] and query["sources"][0]["provenance"] == source["provenance"]
    assert "качество извлечения отдельно не проверено" in query["text"]
    assert not store.get_document("other-run", result["doc_id"])
    forged = {**source, "provenance": {**source["provenance"], "raw_sha256": "0" * 64}}
    assert not receipts.valid_source(forged)


@pytest.mark.parametrize("change", ["local_bytes", "remote_bytes", "redirect", "url", "missing_text", "empty", "offline", "html"])
def test_unverified_original_or_extraction_never_creates_read_source(document, change):
    _, original, extraction, payload, raw = document
    if change == "local_bytes": original.write_bytes(b"other")
    elif change == "remote_bytes": raw["content"] = b"changed online"
    elif change == "redirect": raw["final_url"] = "https://example.org/new.pdf"
    elif change == "url": payload["source"]["requested_url"] = "https://example.org/other.pdf"
    elif change == "missing_text": payload.pop("text")
    elif change == "empty": payload["text"] = "\n"
    elif change == "offline": raw.update(ok=False, error="timeout")
    elif change == "html": raw["mime"] = "text/html"
    extraction.write_text(json.dumps(payload), encoding="utf-8")
    result = fetch(document)
    assert not result["ok"] and not result["sources"]
    assert not store.has_documents("skill-source")


def test_partial_extraction_is_labelled_and_source_text_fits_context(document):
    document[3].update(complete=False, text="Extracted page. " * 5000, ocr_required=[2])
    document[2].write_text(json.dumps(document[3]), encoding="utf-8")
    result = fetch(document, max_chars=50000)
    assert result["ok"] and len(result["text"]) <= 12000
    assert "Документ прочитан частично" in result["text"]
    assert result["provenance"]["complete"] is False
    for source in result["sources"]:
        assert receipts.valid_source(source)
        assert source["quote"] in result["text"]
        assert retrieval.verify_quote("skill-source", result["doc_id"], source["quote"], source["offset"])["quote_verified"]


def test_identical_text_different_originals_never_share_provenance(document):
    first = fetch(document)
    document[1].write_bytes(b"%PDF second original")
    document[4]["content"] = document[1].read_bytes()
    document[3]["source"]["sha256"] = hashlib.sha256(document[1].read_bytes()).hexdigest()
    document[2].write_text(json.dumps(document[3]), encoding="utf-8")
    second = fetch(document)
    assert first["doc_id"] != second["doc_id"]
    assert len(store.list_documents("skill-source")) == 2
    assert fetch(document)["doc_id"] == second["doc_id"]


def test_no_storage_policy_prevents_import_and_network(document, monkeypatch):
    monkeypatch.setattr("webskill.context.web_cache_write_allowed", lambda _: False)
    monkeypatch.setattr(corpus, "_fetch_raw", lambda _: pytest.fail("network must not run"))
    result = fetch(document)
    assert not result["ok"] and "disabled" in result["text"]
    assert not store.has_documents("skill-source")


def test_additive_provenance_migration_preserves_existing_html(document):
    text = "Existing HTML evidence"
    store.store_document(run_id="existing", doc={"doc_id": "old", "url": "https://example.org/old",
                        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                        "canonical_text": text, "nbytes": len(text), "mime": "text/html"},
                         chunks=corpus._chunk(text))
    with sqlite3.connect(store._db_path()) as connection:
        connection.execute("ALTER TABLE documents DROP COLUMN provenance")
    assert fetch(document)["ok"]
    old = store.get_document("existing", "old")
    assert old["canonical_text"] == text and old["provenance"] == {}
