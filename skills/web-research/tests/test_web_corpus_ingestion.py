from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from webskill.application.web_evidence import corpus, retrieval
from webskill.application.web_evidence.freshness import extract_dates
from webskill.infrastructure.web_corpus import store


@pytest.mark.parametrize("html,header,expected", [
    ('<div class="post_news__date">Воскресенье, 04 Окт 2026, 10:01</div>'
     '<article><p>Inflation report.</p></article>', None, {"published": "2026-10-04"}),
    ('<main><p>Statistics for September 2026; week 5–11 October 2026.'
     ' An event happened on 2026-10-04.</p></main>', None, {}),
    ('<article><time datetime="2026-10-04">Event time</time></article>', None, {}),
    ('<div class="post_news__date">31 Фев 2026, 10:01</div>', None, {}),
    ('<time itemprop="datePublished" datetime="2026-10-04T10:01:00+06:00">'
     '4 October 2026</time>', None, {"published": "2026-10-04"}),
    ('<time pubdate datetime="2026-10-04T10:01:00+06:00"></time>', None,
     {"published": "2026-10-04"}),
    ('<script type="application/ld+json">{"@type":"NewsArticle",'
     '"datePublished":"2026-10-04T10:01:00+06:00","dateModified":"2026-10-11"}'
     '</script>', None, {"published": "2026-10-04"}),
    ('<script type="application/ld+json">{"@graph":[{"@type":["NewsArticle"],'
     '"datePublished":"2026-10-04"}]}</script>', None, {"published": "2026-10-04"}),
    ('<script type="application/ld+json">{"@type":"Organization",'
     '"datePublished":"2026-10-04"}</script>', None, {}),
    ('<script type="application/ld+json">{"@graph":['
     '{"@type":"NewsArticle","datePublished":"2026-10-04"},'
     '{"@type":"NewsArticle","datePublished":"2026-10-08"}]}</script>', None, {}),
    ('<time itemprop="datePublished" datetime="2026-10-08"></time>'
     '<div class="post_news__date">04 Окт 2026, 10:01</div>', None, {}),
    ('<meta property="article:published_time" content="2026-10-08T09:00:00+06:00">'
     '<meta property="article:modified_time" content="2026-10-11">'
     '<div class="post_news__date">04 Окт 2026, 10:01</div>',
     'Mon, 12 Oct 2026 00:00:00 GMT', {"published": "2026-10-08", "modified": "2026-10-11"}),
], ids=["visible-outside-article", "unknown-prose", "untyped-event", "invalid-calendar",
        "typed-time", "pubdate-time", "news-json", "news-graph", "unrelated-json",
        "conflicting-news-json", "conflicting-visible-dates", "meta-precedence"])
def test_publication_date_requires_explicit_unambiguous_source_evidence(html, header, expected):
    assert extract_dates(html, header) == expected


@pytest.mark.parametrize("container", ["footer", "nav", "aside"])
def test_navigation_dates_do_not_date_an_undated_article(container):
    html = ('<main><article><p>Undated article.</p></article></main>'
            f'<{container}><div class="post_news__date">04 Окт 2026, 10:01</div>'
            '<time itemprop="datePublished" datetime="2026-10-04"></time>'
            '<script type="application/ld+json">{"@type":"NewsArticle",'
            f'"datePublished":"2026-10-04"}}</script></{container}>')
    assert extract_dates(html, None) == {}


def test_visible_article_date_survives_corpus_ingest_query_and_quote_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_DB_PATH_OVERRIDE", str(tmp_path / "corpus.sqlite3"))
    monkeypatch.setattr(retrieval, "_embed_rerank", lambda *args: None)
    html = ('<html><head><title>Inflation report</title></head><body>'
            '<div class="post_news__date">Воскресенье, 04 Окт 2026, 10:01</div>'
            '<article><p>The violet telescope reports inflation for September.</p></article>'
            '</body></html>')
    monkeypatch.setattr(corpus, "_fetch_raw", lambda url: {
        "ok": True, "final_url": url, "mime": "text/html", "charset": "utf-8",
        "content": html.encode("utf-8"), "last_modified": None,
    })
    passport = corpus.ingest("https://example.org/inflation", "article-date-run")
    assert passport["ok"] and passport["dates"] == {"published": "2026-10-04"}
    hit = retrieval.web_query("article-date-run", "violet telescope")["results"][0]
    assert hit["dates"] == passport["dates"]
    assert retrieval.verify_quote(
        "article-date-run", hit["doc_id"], hit["quote"], hit["offset"]
    )["quote_verified"]


def test_excerpt_includes_specific_match_after_common_term(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(store, "_DB_PATH_OVERRIDE", str(tmp_path / "corpus.sqlite3"))
    text = "Path describes filesystem objects. " + "Other details. " * 65 + (
        "Path.read_text(encoding=None) returns the contents as a string. "
        "The encoding parameter selects the text encoding, such as utf-8."
    ) + " More notes." * 25
    monkeypatch.setattr(corpus, "_fetch_raw", lambda url: {
        "ok": True, "final_url": url, "mime": "text/plain", "charset": "utf-8",
        "content": text.encode("utf-8"), "last_modified": None,
    })
    passport = corpus.ingest("https://example.com/pathlib", "excerpt-run")
    assert passport["ok"]
    monkeypatch.setattr(retrieval, "_embed_rerank", lambda *a: None)
    result = retrieval.web_query("excerpt-run", "Path.read_text encoding", top_k=1)
    hit = result["results"][0]
    assert "read_text(encoding=None)" in hit["quote"]
    assert "selects the text encoding" in hit["quote"]
    assert retrieval.verify_quote("excerpt-run", hit["doc_id"], hit["quote"], hit["offset"])["quote_verified"]


def test_ingest_query_and_quote_verification_round_trip(tmp_path: Path) -> None:
    previous_override = store._DB_PATH_OVERRIDE
    store._DB_PATH_OVERRIDE = str(tmp_path / "web_corpus.sqlite3")
    html = b"""
        <html><head><title>Elira Corpus</title><script>ignore me</script></head>
        <body><h1>Corpus ingestion</h1>
        <p>The violet telescope is the unique retrieval marker.</p></body></html>
    """
    fetched = {
        "ok": True,
        "final_url": "https://example.com/docs",
        "mime": "text/html",
        "charset": "utf-8",
        "content": html,
        "last_modified": None,
    }
    try:
        with patch.object(corpus, "_fetch_raw", return_value=fetched):
            passport = corpus.ingest("https://example.com/docs", "run-a")

        assert passport["ok"] is True
        assert passport["n_chunks"] >= 1
        assert passport["title"] == "Elira Corpus"
        document = store.get_document("run-a", passport["doc_id"])
        assert document is not None
        assert "ignore me" not in document["canonical_text"]

        with patch.object(retrieval, "_embed_rerank", return_value=None):
            result = retrieval.web_query("run-a", "violet telescope", top_k=3)

        assert result["ok"] is True
        assert result["ranker"] == "bm25"
        assert result["results"]
        hit = result["results"][0]
        assert hit["url"] == "https://example.com/docs"
        assert "violet telescope" in hit["quote"]
        assert retrieval.verify_quote(
            "run-a",
            hit["doc_id"],
            hit["quote"],
            offset=hit["offset"],
        )["quote_verified"] is True
        assert retrieval.web_query("run-b", "violet telescope")["results"] == []
    finally:
        store._DB_PATH_OVERRIDE = previous_override


def test_refetch_updates_dates_without_erasing_known_dates_or_using_another_url(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_DB_PATH_OVERRIDE", str(tmp_path / "corpus.sqlite3"))
    monkeypatch.setattr(retrieval, "_embed_rerank", lambda *args: None)
    metadata = '<meta name="date" content="2025-02-14"><meta name="lastmod" content="2025-03-01">'
    monkeypatch.setattr(corpus, "_fetch_raw", lambda url: {
        "ok": True, "final_url": url, "mime": "text/html", "charset": "utf-8",
        "content": f"<html><head>{metadata}</head><body>Violet telescope release.</body></html>".encode(),
        "last_modified": None,
    })
    url = "https://www.python.org/release-notes"
    original = corpus.ingest(url, "refetch-run")
    metadata = '<meta name="lastmod" content="2025-03-19">'
    refreshed = corpus.ingest(url, "refetch-run")
    assert refreshed["deduped"] is True
    assert refreshed["doc_id"] == original["doc_id"]
    hit = retrieval.web_query("refetch-run", "violet telescope")["results"][0]
    assert hit["dates"] == {"published": "2025-02-14", "modified": "2025-03-19"}

    metadata = ""
    assert corpus.ingest(url, "refetch-run")["deduped"] is True
    metadata = '<meta name="date" content="2024-01-01"><meta name="lastmod" content="2024-02-02">'
    assert corpus.ingest("https://example.org/mirror", "refetch-run")["deduped"] is True
    hit = retrieval.web_query("refetch-run", "violet telescope")["results"][0]
    assert hit["url"] == url
    assert hit["dates"] == {"published": "2025-02-14", "modified": "2025-03-19"}
    assert hit["tier"] == "official"
