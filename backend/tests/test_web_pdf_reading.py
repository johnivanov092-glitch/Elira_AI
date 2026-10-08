"""PDFs require an explicit document-read skill; failed fetches are never read sources."""
from types import SimpleNamespace

import pytest

from app.application.code_agent.tools import _web
from app.application.web_evidence import corpus
from app.infrastructure.search.web_runtime import fetch_page


@pytest.fixture
def pdf_http(monkeypatch):
    calls = []
    monkeypatch.setattr("app.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(content=b"%PDF-1.7 opaque binary", status_code=200, url=url,
                               headers={"Content-Type": "application/pdf"}, close=lambda: None)
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(_web, "_render_fallback",
                        lambda *a: pytest.fail("Binary document must not enter browser fallback"))
    return calls


@pytest.mark.parametrize("fragment", ["", "#page=2"])
def test_pdf_fetch_directs_to_skill_without_parsing_or_source_receipt(pdf_http, fragment):
    url = "https://example.org/paper.pdf" + fragment
    page = fetch_page(url)
    assert not page.ok and page.text == ""
    assert "processing_required" in page.error and "document-read" in page.error
    assert page.final_url == "https://example.org/paper.pdf"
    result = _web.tool_web_fetch(url=url)
    assert result["ok"] is False
    assert "document-read" in result["text"] and "%PDF" not in result["text"]
    assert not any(s.get("status") == "read" or s.get("quote") for s in result.get("sources", []))


@pytest.mark.parametrize("mime", [
    "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
])
def test_binary_ingestion_requires_skill_and_never_stores_error_as_corpus(monkeypatch, mime):
    monkeypatch.setattr(corpus, "_fetch_raw", lambda url: {
        "ok": True, "final_url": url, "mime": mime,
        "content": b"opaque document bytes", "last_modified": None,
    })
    monkeypatch.setattr("app.infrastructure.web_corpus.store.store_document",
                        lambda *a, **k: pytest.fail("Unparsed binary must not be stored as a source"))
    result = corpus.ingest("https://example.org/document", "binary-run")
    assert result["ok"] is False and result["error"] == "processing_required"
    assert result["skill"] == "document-read"
    assert result["final_url"] == "https://example.org/document"
    assert "doc_id" not in result
    monkeypatch.setattr(_web, "_current_run_id", lambda: "binary-run")
    monkeypatch.setattr("app.application.code_agent.loop_helpers.run_persistence_policy",
                        lambda _run: {"rag": True})
    monkeypatch.setattr("app.application.web_evidence.availability.begin", lambda *a, **k: {})
    monkeypatch.setattr("app.application.web_evidence.availability.finish", lambda *a, **k: None)
    shown = _web.tool_web_fetch(url="https://example.org/document", store=True)
    assert not shown["ok"] and "read_document.py --url" in shown["text"]
    assert not any(s.get("status") == "read" or s.get("quote") for s in shown.get("sources", []))
