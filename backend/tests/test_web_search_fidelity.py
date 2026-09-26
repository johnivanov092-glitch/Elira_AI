import sys
from pathlib import Path
from urllib.parse import quote
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.web import search_web
from app.core import web_engines as engines


def test_ddgs_text_uses_supported_web_backends_without_encyclopedia_short_circuit():
    from ddgs.engines import ENGINES

    ddgs = MagicMock()
    ddgs.__enter__.return_value.text.return_value = []
    with patch.object(engines, "DDGS", return_value=ddgs):
        engines.search_duckduckgo("latest project release", max_results=5)
    call = ddgs.__enter__.return_value.text.call_args
    requested = call.kwargs.get("backend", "auto").split(",")
    assert len(requested) >= 2
    assert set(requested) <= set(ENGINES["text"])
    assert not {"auto", "all", "wikipedia", "grokipedia"} & set(requested)
    assert "wikipedia" in engines.DEFAULT_SEARCH_ENGINES


def test_search_preserves_encoded_destination_through_redirect_and_dedupe():
    destination = "https://example.com/a%2Fb?q=x%26admin%3D1#part%202"
    ddgs = MagicMock()
    ddgs.__enter__.return_value.text.return_value = [{
        "title": "Encoded link", "href": "/url?q=" + quote(destination, safe=""),
        "body": "Exact destination",
    }]
    with patch.object(engines, "DDGS", return_value=ddgs), patch.dict(
        "app.core.web.ENGINE_FUNCS",
        {"duckduckgo": engines.search_duckduckgo, "wikipedia": lambda *a, **k: []},
        clear=True,
    ):
        results = search_web("encoded link", engines=["duckduckgo"])
    assert results[0]["href"] == destination


def test_explicit_site_constraint_survives_provider_fallback():
    rows = [
        {"title": "Клиника Алматы", "href": url, "body": "Клиника Алматы", "engine": "duckduckgo"}
        for url in (
            "https://ants.kz/services/", "https://news.ants.kz/latest",
            "https://ants.kz.evil.example/", "https://example.com/ants.kz",
            "https://xn--d1abbgf6aiiy.xn--p1ai/news/",
        )
    ]
    with patch.dict(
        "app.core.web.ENGINE_FUNCS",
        {"duckduckgo": lambda *a, **k: rows, "wikipedia": lambda *a, **k: []},
        clear=True,
    ):
        results = search_web("Клиника Алматы site:ants.kz -site:news.ants.kz", engines=["duckduckgo"])
        idn_results = search_web("новости site:президент.рф", engines=["duckduckgo"])
    assert [row["href"] for row in results] == ["https://ants.kz/services/"]
    assert [row["href"] for row in idn_results] == ["https://xn--d1abbgf6aiiy.xn--p1ai/news/"]


def test_search_retains_date_and_marks_filters_per_provider():
    response = MagicMock()
    response.json.return_value = {"results": [{
        "title": "Release", "url": "https://example.com/release",
        "content": "Release date", "publishedDate": "2026-09-19T08:00:00Z",
    }]}
    client = MagicMock()
    client.get.return_value = response
    fallback = [{"title": "Older release", "href": "https://example.net/old",
                 "body": "Release", "engine": "duckduckgo"}]
    with patch.dict("os.environ", {"SEARXNG_URL": "http://search.local"}), patch.object(
        engines, "session", return_value=client,
    ), patch.dict("app.core.web.ENGINE_FUNCS", {
        "searxng": engines.search_searxng, "duckduckgo": lambda *a, **k: fallback,
        "wikipedia": lambda *a, **k: [],
    }, clear=True):
        results = search_web("release", categories="news", time_range="week")
    by_engine = {row["engine"]: row for row in results}
    assert by_engine["searxng"]["date"] == "2026-09-19T08:00:00Z"
    assert by_engine["searxng"]["filter_time_range"] == "week"
    assert by_engine["searxng"]["filter_categories"] == "news"
    assert "filter_time_range" not in by_engine["duckduckgo"]
