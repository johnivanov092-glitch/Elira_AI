from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.web_evidence import corpus, retrieval
from app.infrastructure.web_corpus import store


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
