"""Preserve destinations from page navigation without reading linked pages."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent.tools import _web


def _response(html, url="https://example.org/guide/page"):
    return SimpleNamespace(text=html, status_code=200, url=url,
                           encoding="utf-8", headers={}, close=Mock())


def test_fetch_keeps_distinct_document_links_without_reading_them(monkeypatch):
    # Labels and destinations observed in the SI audit; markup is synthetic.
    english = "https://www.bipm.org/documents/20126/41483022/SI-Brochure-9-EN.pdf"
    complete = "https://www.bipm.org/documents/20126/41483022/SI-Brochure-9.pdf/fcf090b2-04e6-88cc-1149-c3e029ad8232?download=true&version=6.1"
    html = ("<main><h1>SI brochure</h1><p>" + "Publication information. " * 12 + "</p>"
            f"<a href='{english}'>Text in English</a>"
            f"<a href='{complete}'>Complete brochure</a></main>")
    get = Mock(return_value=_response(html))
    monkeypatch.setattr("requests.get", get)
    result = _web.tool_web_fetch(url="https://example.org/guide/page")
    assert result["ok"] is True
    assert result["pages"][0]["links"] == [
        {"label": "Text in English", "url": english},
        {"label": "Complete brochure", "url": complete},
    ]
    assert english in result["text"] and complete in result["text"]
    assert result["text"].index("<<<DATA") < result["text"].index(complete) < result["text"].rindex("DATA>>>")
    assert {source["url"] for source in result["sources"]} == {"https://example.org/guide/page"}
    assert all(english not in source["quote"] for source in result["sources"])
    get.assert_called_once()


def test_fetch_resolves_page_base_without_unquoting_signed_links(monkeypatch):
    html = ("<head><base href='/docs/'></head><nav><a href='/noise'>Noise</a></nav>"
            "<main><p>" + "Release information. " * 15 + "</p>"
            "<a href='download/a%2Fb?q=x%26y%3Dz'>Current release</a>"
            "<a href='javascript:alert(1)'>Bad script</a>"
            "<a href='mailto:user@example.org'>Email</a>"
            "<a href='https://user:secret@example.org/private'>Credentials</a>"
            "<a href='https://[bad'>Invalid URL</a>"
            "<a href='https://example.org:bad/path'>Invalid port</a></main>")
    final_url = "https://example.org/redirected/page"
    monkeypatch.setattr("requests.get", lambda *a, **k: _response(html, final_url))
    result = _web.tool_web_fetch(url="https://example.org/guide/page")
    assert result["pages"][0]["links"] == [
        {"label": "Current release", "url": "https://example.org/docs/download/a%2Fb?q=x%26y%3Dz"},
    ]
    assert result["pages"][0]["links_truncated"] is False
    assert "secret" not in result["text"]


def test_fetch_marks_bounded_link_manifest(monkeypatch):
    html = ("<main><p>" + "Release directory. " * 15 + "</p>" + "".join(
        f"<a href='/release/{number}'>Release {number}</a>" for number in range(80)
    ) + "</main>")
    monkeypatch.setattr("requests.get", lambda *a, **k: _response(html))
    result = _web.tool_web_fetch(url="https://example.org/guide/page")
    assert 0 < len(result["pages"][0]["links"]) <= 20
    assert result["pages"][0]["links_truncated"] is True
    assert "Список ссылок сокращён" in result["text"]


def test_browser_fallback_retains_links_from_rendered_main_dom(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _response("<main>Loading</main>"))
    target = "https://www.firefox.com/en-US/releases/"
    html = ("<main><p>" + "Current channel information. " * 15 + "</p>"
            f"<a href='{target}'>Release Notes</a></main>")
    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(status=200)),
        title=AsyncMock(return_value="Releases"), url="https://example.org/current",
        inner_text=AsyncMock(return_value="Current channel information. " * 15),
        content=AsyncMock(return_value=html),
    )
    browser = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("playwright.async_api.async_playwright", Playwright)
    result = _web.tool_web_fetch(url="https://example.org/guide/page")
    assert result["pages"][0]["links"] == [{"label": "Release Notes", "url": target}]
    assert "JS" in result["text"] and target in result["text"]
    assert {source["url"] for source in result["sources"]} == {"https://example.org/current"}


def test_store_fetch_preserves_links_in_passport_without_verifying_targets(monkeypatch, tmp_path):
    from app.application.web_evidence import corpus
    from app.infrastructure.web_corpus import store

    monkeypatch.setattr(store, "_DB_PATH_OVERRIDE", str(tmp_path / "links.sqlite3"))
    monkeypatch.setattr(_web, "_current_run_id", lambda: "links-run")
    url = "https://example.org/guide/page"
    target = "https://example.org/current?q=a%26b"
    html = ("<nav><a href='/noise'>Navigation noise</a></nav><main><h1>Current channel</h1><p>"
            + "Release information. " * 15 + f"</p><a href='{target}'>Release Notes</a></main>")
    monkeypatch.setattr(corpus, "_fetch_raw", lambda url: {
        "ok": True, "final_url": url, "mime": "text/html", "charset": "utf-8",
        "content": html.encode("utf-8"), "last_modified": None,
    })
    passport = corpus.ingest(url, "links-run")
    assert passport["links"] == [{"label": "Release Notes", "url": target}]
    assert passport["links_truncated"] is False
    result = _web.tool_web_fetch(url=url, store=True)
    assert target in result["text"]
    assert result["text"].index("<<<DATA") < result["text"].index(target) < result["text"].rindex("DATA>>>")
    assert "Navigation noise" not in result["text"]
    assert {source["url"] for source in result["sources"]} == {url}
    assert all(source["status"] == "fetched" and not source["quote_verified"] for source in result["sources"])


def test_batch_fetch_keeps_every_page_excerpt_and_links_through_llm_packing(monkeypatch):
    from app.application.code_agent.loop_helpers import TOOL_RESULT_LLM_LIMIT, _truncate_for_llm
    from app.application.code_agent.run_evidence import RunEvidence
    from app.infrastructure.search.web_runtime import PageFetchResult

    urls = [f"https://example.org/page-{number}" for number in range(4)]
    def fetch(url, limit):
        if url == urls[-1]:
            return PageFetchResult(final_url=url, status_code=404, error="HTTP 404")
        return PageFetchResult(final_url=url, status_code=200,
                               text=f"Facts for {url}. " + "Detailed page content. " * 400,
                               links=(("Release index", url + "/current"),))
    monkeypatch.setattr(_web, "_fetch_one", fetch)
    result = _web.tool_web_fetch(urls=urls)
    packed = _truncate_for_llm(result["text"])
    assert len(result["text"]) <= TOOL_RESULT_LLM_LIMIT
    assert packed == result["text"]
    for url in urls[:-1]:
        assert f"Facts for {url}." in packed
        assert url + "/current" in packed
    assert "HTTP 404" in packed
    assert all(page["truncated"] for page in result["pages"][:-1])
    evidence = RunEvidence(sources=result["sources"])
    evidence.mark_sources_presented([{"role": "tool", "content": packed}])
    excerpts = [source for source in evidence.sources if source["status"] == "excerpt"]
    assert {source["url"] for source in excerpts} == set(urls[:-1])
    assert all(source["presented"] and source["quote_verified"] for source in excerpts)
