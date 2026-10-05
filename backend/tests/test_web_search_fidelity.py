import sys
from pathlib import Path
from urllib.parse import quote
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.web import search_web
from app.core import web_engines as engines


def test_legacy_preferences_cannot_activate_removed_search_adapters():
    retired = MagicMock(side_effect=AssertionError("retired provider executed"))
    primary = MagicMock(return_value=[])
    with patch.dict("app.core.web.ENGINE_FUNCS", {
        "searxng": primary, "duckduckgo": retired, "wikipedia": retired,
    }, clear=True):
        assert search_web("latest release", engines=["duckduckgo", "wikipedia"]) == []
    primary.assert_called_once()
    retired.assert_not_called()
    assert engines.DEFAULT_SEARCH_ENGINES == ("searxng",)


def test_search_preserves_encoded_destination_through_redirect_and_dedupe():
    destination = "https://example.com/a%2Fb?q=x%26admin%3D1#part%202"
    client = MagicMock()
    client.get.return_value.json.return_value = {"results": [{
        "title": "Encoded link", "url": "/url?q=" + quote(destination, safe=""),
        "content": "Exact destination",
    }]}
    with patch.dict("os.environ", {"SEARXNG_URL": "http://search.local"}), patch.object(
        engines, "session", return_value=client,
    ):
        results = search_web("encoded link")
    assert results[0]["href"] == destination
    client.get.assert_called_once()
    assert client.get.call_args.args[0] == "http://search.local/search"


def test_explicit_site_constraint_filters_searxng_results():
    rows = [
        {"title": "Клиника Алматы", "href": url, "body": "Клиника Алматы", "engine": "searxng"}
        for url in (
            "https://ants.kz/services/", "https://news.ants.kz/latest",
            "https://ants.kz.evil.example/", "https://example.com/ants.kz",
            "https://xn--d1abbgf6aiiy.xn--p1ai/news/",
        )
    ]
    with patch.dict(
        "app.core.web.ENGINE_FUNCS",
        {"searxng": lambda *a, **k: rows},
        clear=True,
    ):
        results = search_web("Клиника Алматы site:ants.kz -site:news.ants.kz")
        idn_results = search_web("новости site:президент.рф")
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
    with patch.dict("os.environ", {"SEARXNG_URL": "http://search.local"}), patch.object(
        engines, "session", return_value=client,
    ), patch.dict("app.core.web.ENGINE_FUNCS", {
        "searxng": engines.search_searxng,
    }, clear=True):
        results = search_web("release", categories="news", time_range="week")
    by_engine = {row["engine"]: row for row in results}
    assert by_engine["searxng"]["date"] == "2026-09-19T08:00:00Z"
    assert by_engine["searxng"]["filter_time_range"] == "week"
    assert by_engine["searxng"]["filter_categories"] == "news"
    assert set(by_engine) == {"searxng"}


def test_legacy_pipeline_reports_missing_backend_without_direct_fallback():
    from app.infrastructure.search.web_runtime import do_web_search_legacy

    timeline, results = [], []
    with patch.dict("os.environ", {"SEARXNG_URL": ""}), patch.object(engines, "session") as client:
        context = do_web_search_legacy("latest release", timeline, results,
                                       clean_query_func=lambda query: query)
    client.assert_not_called()
    assert results[-1]["result"]["ok"] is False
    assert "SEARXNG_URL" in results[-1]["result"]["error"]
    assert "SEARXNG_URL" in context


def test_wikipedia_url_is_an_ordinary_searxng_result_without_direct_search():
    url = "https://en.wikipedia.org/wiki/Metasearch_engine"
    client = MagicMock()
    client.get.return_value.json.return_value = {"results": [{
        "title": "Metasearch engine", "url": url, "content": "Reference source",
    }]}
    with patch.dict("os.environ", {"SEARXNG_URL": "http://search.local"}), patch.object(
        engines, "session", return_value=client,
    ):
        results = search_web("metasearch engine")
    assert results[0]["href"] == url
    client.get.assert_called_once()
    assert client.get.call_args.args[0] == "http://search.local/search"


def test_legacy_pipeline_retains_news_failure_with_healthy_main_results():
    from app.infrastructure.search.web_runtime import do_web_search_legacy

    timeline, results = [], []
    row = {"title": "Release", "href": "https://example.com/release",
           "body": "Verified release", "engine": "searxng"}
    with patch("app.core.web.search_web", return_value=[row]), patch(
        "app.core.web.search_news", side_effect=RuntimeError("SearXNG news HTTP503"),
    ), patch("app.core.web.fetch_page_text", return_value="Release details. " * 10):
        context = do_web_search_legacy("release", timeline, results,
                                       clean_query_func=lambda query: query)

    result = results[-1]["result"]
    assert result["found"] == 1
    assert result["partial"] is True
    assert result["error"] == "SearXNG news HTTP503"
    assert "SearXNG news HTTP503" in context
    assert "Release details" in context
    assert "SearXNG news HTTP503" in str(timeline[-1])
