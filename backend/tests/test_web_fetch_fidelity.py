"""Public web_fetch regressions with only HTTP/browser boundaries replaced."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent.tools import _web


def _response(html, *, status=200, url="https://example.org/page", headers=None):
    return SimpleNamespace(text=html, status_code=status, url=url,
                           encoding="utf-8", headers=headers or {}, close=Mock())


def _browser(monkeypatch, *, text, status=200, url="https://example.org/page", html=None):
    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(status=status, headers={})),
        title=AsyncMock(return_value="Example"), url=url,
        inner_text=AsyncMock(return_value=text),
        content=AsyncMock(return_value=html or f"<main>{text}</main>"),
    )
    browser = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("playwright.async_api.async_playwright", Playwright)
    return page, browser


def test_web_fetch_preserves_short_dates_prices_and_table_values(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _response(
        "<main><h1>Release 3.14.7</h1><time>2026-09-19</time>"
        "<table><tr><th>Currency</th><th>Price</th></tr>"
        "<tr><td>USD</td><td>485.20</td></tr></table>"
        "<p>" + "Availability and release information. " * 8 + "</p></main>"))
    result = _web.tool_web_fetch(url="https://example.org/page")
    assert result["ok"] is True
    assert all(value in result["text"] for value in ("3.14.7", "2026-09-19", "USD", "485.20"))


def test_raw_plain_text_code_retains_indentation_and_angle_brackets(monkeypatch):
    code = 'def keep(engine):\n    if engine < 3:\n        return "<tag>"\n\n    return None\n'
    monkeypatch.setattr("requests.get", lambda *a, **k: _response(code, headers={"Content-Type": "text/plain; charset=utf-8"}))
    monkeypatch.setattr(_web, "_render_fallback", lambda *a: pytest.fail("Plain text does not need a browser"))
    result = _web.tool_web_fetch(url="https://example.org/raw.py")
    assert result["ok"] and result["pages"][0]["mime"] == "text/plain"
    assert result["sources"][0]["quote"] == code


@pytest.mark.parametrize("phrase,found", [("NOT recommended", True), ("no such phrase", False)])
def test_find_preserves_context_offsets_and_never_fakes_a_missing_match(monkeypatch, phrase, found):
    text = "Unrelated details. " * 4000 + "Treatment is NOT\nrecommended for this population.\n" + "Appendix. " * 4000
    monkeypatch.setattr("requests.get", lambda *a, **k: _response(text, headers={"Content-Type": "text/plain"}))
    result = _web.tool_web_fetch(url="https://example.org/reference.txt", find=phrase, max_chars=50000)
    assert result["ok"] is found
    assert len(result["text"]) <= 12000
    if found:
        assert "Treatment is NOT\nrecommended" in result["text"]
        for source in result["sources"]:
            assert source["quote"] == text[source["offset"]:source["offset"] + len(source["quote"])]
        assert result["pages"][0]["text_offset"] > 60000
    else:
        assert "find phrase not found" in result["text"]
        assert all(source["status"] == "failed" and not source["quote_verified"] for source in result["sources"])


@pytest.mark.parametrize("options", [{"find": []}, {"find": "x" * 201}, {"find": "result", "store": True},
                                     {"find": "result", "urls": ["https://example.org/a"]}])
def test_invalid_find_request_is_rejected_before_network(monkeypatch, options):
    monkeypatch.setattr("requests.get", lambda *a, **k: pytest.fail("Invalid request must not access network"))
    assert not _web.tool_web_fetch(url="https://example.org/a", **options)["ok"]


@pytest.mark.parametrize("viewport,wait_until", [(None, "domcontentloaded"), ("mobile", "networkidle")])
def test_passive_browser_does_not_wait_for_ad_network_idle(monkeypatch, viewport, wait_until):
    monkeypatch.setattr("app.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    page, _ = _browser(monkeypatch, text="Actual readable source text.")
    if viewport:
        page.evaluate = AsyncMock(return_value={"sw": 375, "cw": 375})
    result = _web.tool_browser(url="https://example.org/page", viewport=viewport)
    assert result["ok"]
    assert page.goto.call_args.kwargs["wait_until"] == wait_until


@pytest.mark.parametrize("anchor", ["<h2 id='prices'>Prices</h2>", "<a name='prices'></a><h2>Prices</h2>"])
def test_fragment_fetch_starts_at_requested_section_not_page_start(monkeypatch, anchor):
    html = ("<main><h1>Intro</h1><p>" + "Irrelevant introduction. " * 80 + "</p>"
            + anchor + "<p>USD 485.20. " + "Requested section details. " * 20
            + "</p><h2>Unrelated section</h2><p>Do not include this.</p></main>")
    monkeypatch.setattr("requests.get", lambda *a, **k: _response(html))
    result = _web.tool_web_fetch(url="https://example.org/page#prices", max_chars=500)
    assert result["ok"] is True
    assert "485.20" in result["text"]
    assert "Irrelevant introduction" not in result["text"]
    assert "Unrelated section" not in result["text"]
    assert result["pages"][0]["truncated"] is True
    assert "Доступные разделы: #prices" in result["text"]


def test_missing_fragment_lists_real_sections_without_browser_retry(monkeypatch):
    html = ("<main><h1 id='intro'>Introduction</h1><p>" + "Documentation content. " * 12
            + "</p><a name='enabling'></a><h2>Enable foreign keys</h2>"
            "<p>PRAGMA foreign_keys = ON;</p><dt id='read_text'>read_text()</dt>"
            "<dd>Read the text.</dd><a name=''></a></main>")
    monkeypatch.setattr("requests.get", lambda *a, **k: _response(html))
    page, _ = _browser(monkeypatch, text="irrelevant browser body")
    result = _web.tool_web_fetch(url="https://example.org/page#enabling_foreign_keys")
    assert result["ok"] is False
    assert "Доступные разделы: #intro, #enabling, #read_text" in result["text"]
    assert result["pages"][0]["available_fragments"] == ["intro", "enabling", "read_text"]
    assert {source["status"] for source in result["sources"]} == {"failed"}
    page.goto.assert_not_awaited()


def test_static_http_error_never_becomes_a_verified_excerpt(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _response("404 Not Found", status=404))
    page, _ = _browser(monkeypatch, text="404 Not Found", status=404)
    result = _web.tool_web_fetch(url="https://example.org/missing")
    assert result["ok"] is False
    assert result["sources"][0]["status"] == "failed"
    assert not result["sources"][0]["quote_verified"]
    assert "HTTP 404" in result["text"]
    page.goto.assert_not_awaited()


@pytest.mark.parametrize("status", [404, 503])
def test_browser_http_error_never_upgrades_empty_static_html(monkeypatch, status):
    monkeypatch.setattr("requests.get", lambda *a, **k: _response("<body></body>"))
    _, browser = _browser(monkeypatch, text=f"HTTP {status} Error", status=status)
    result = _web.tool_web_fetch(url="https://example.org/page")
    assert result["ok"] is False
    assert result["sources"][0]["status"] == "failed"
    assert f"HTTP {status}" in result["text"]
    browser.close.assert_awaited_once()


def test_healthy_thin_js_fallback_retains_final_url(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _response("<main>Loading</main>"))
    final_url = "https://example.org/current"
    _browser(monkeypatch, text="Rendered price USD 485.20. " * 15, url=final_url)
    result = _web.tool_web_fetch(url="https://example.org/page")
    assert result["ok"] is True
    assert "485.20" in result["text"] and "JS" in result["text"]
    assert {source["url"] for source in result["sources"]} == {final_url}


def test_failed_browser_fallback_preserves_healthy_short_static_fact(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _response("<main>USD 485.20</main>"))
    _browser(monkeypatch, text="Forbidden", status=403)
    result = _web.tool_web_fetch(url="https://example.org/page")
    assert result["ok"] is True
    assert "USD 485.20" in result["text"]
    assert "Forbidden" not in result["text"]
    assert result["pages"][0]["status_code"] == 200
    assert {source["status"] for source in result["sources"]} == {"excerpt"}


def test_definition_fragment_includes_description_until_next_definition(monkeypatch):
    html = ("<main><dl><dt id='Path.read_text'>Path.read_text()</dt>"
            "<dd>Returns decoded text. Encoding is optional. "
            + "The file is closed after the read completes. " * 6
            + "</dd><dt id='Path.write_text'>Path.write_text()</dt>"
            "<dd>Overwrites the file.</dd></dl></main>")
    monkeypatch.setattr("requests.get", lambda *a, **k: _response(html))
    _browser(monkeypatch, text="Path.read_text()", html=html,
             url="https://example.org/page#Path.read_text")
    result = _web.tool_web_fetch(url="https://example.org/page#Path.read_text")
    assert result["ok"] is True
    assert "Path.read_text()" in result["text"]
    assert "Encoding is optional" in result["text"]
    assert "closed after the read" in result["text"]
    assert "Path.write_text" not in result["text"]
    assert "Overwrites" not in result["text"]


def test_redirect_receipt_uses_actual_url_and_revalidates_each_hop(monkeypatch):
    requested, final = "https://example.org/old", "https://example.org/new?q=x%26y%3Dz"
    responses = iter([_response("", status=302, url=requested, headers={"Location": final}),
                      _response("<main>" + "Current page content. " * 20 + "</main>", url=final)])
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs["allow_redirects"]))
        return next(responses)

    monkeypatch.setattr("requests.get", get)
    result = _web.tool_web_fetch(url=requested)
    assert result["ok"] is True
    assert calls == [(requested, False), (final, False)]
    assert {source["url"] for source in result["sources"]} == {final}
