from __future__ import annotations

from typing import Any


def tool_web_search(*, query: str, top_k: int = 5) -> dict[str, Any]:
    """Search the web via the configured engines (Tavily / DuckDuckGo /
    Wikipedia). Returns ranked results with title + URL + snippet. Use
    `web_fetch` after this to read the full content of a specific result.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return {"text": "ERROR: query is empty"}
    try:
        from app.infrastructure.search.web_search import search_web
    except Exception as exc:  # pragma: no cover - import path
        return {"text": f"ERROR: web search unavailable: {exc}"}

    limit = max(1, min(int(top_k), 10))
    result = search_web(cleaned, max_results=limit)
    sources = result.get("sources") or []
    if not sources:
        return {"text": f"No web results for '{cleaned}'"}

    engines = ", ".join(result.get("engines_used") or []) or "?"
    lines = [f"Found {len(sources)} results via {engines}:"]
    for i, item in enumerate(sources[:limit], 1):
        title = (item.get("title") or "").strip() or "(no title)"
        url = (item.get("url") or "").strip()
        snippet = (item.get("snippet") or item.get("content") or "").strip()
        if len(snippet) > 350:
            snippet = snippet[:350] + " […]"
        lines.append(f"\n[{i}] {title}\n    {url}\n    {snippet}" if snippet else f"\n[{i}] {title}\n    {url}")
    return {"text": "\n".join(lines)}


def tool_web_fetch(*, url: str, max_chars: int = 8000) -> dict[str, Any]:
    """Fetch a single web page and extract its main readable text.

    HTML noise (nav, footer, ads, scripts) is stripped via the project's
    existing BeautifulSoup-based extractor. Use this AFTER `web_search`
    has surfaced URLs worth reading in full.
    """
    cleaned_url = (url or "").strip()
    if not cleaned_url:
        return {"text": "ERROR: url is empty"}
    if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://")):
        return {"text": f"ERROR: url must start with http:// or https:// — got '{cleaned_url[:80]}'"}

    from app.application.web.ssrf_guard import check_ssrf
    ssrf_reason = check_ssrf(cleaned_url)
    if ssrf_reason:
        return {"text": f"ERROR: SSRF blocked — {ssrf_reason}"}

    try:
        from app.infrastructure.search.web_search import fetch_page_text
    except Exception as exc:  # pragma: no cover
        return {"text": f"ERROR: web fetch unavailable: {exc}"}

    limit = max(500, min(int(max_chars), 50000))
    try:
        body = fetch_page_text(cleaned_url, max_chars=limit)
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}

    body = (body or "").strip()
    if not body:
        return {"text": f"ERROR: empty or non-HTML response from {cleaned_url}"}
    return {"text": f"[fetched: {cleaned_url}]\n\n{body}"}


def _browser_render(url: str, wait_selector: str | None, limit: int) -> tuple[str, str, str]:
    """Render a page with Playwright. Runs in a worker thread (see tool_browser):
    the Playwright sync API must not be called from inside an asyncio loop."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass
            return page.title(), page.url, (page.inner_text("body") or "")[:limit]
        finally:
            browser.close()


def tool_browser(*, url: str, wait_selector: str | None = None, max_chars: int = 8000) -> dict[str, Any]:
    """Open a URL in a real headless browser (Playwright/Chromium), render
    JavaScript, and return the visible page text. Use when `web_fetch` is not
    enough — pages that need JS to render, SPAs, or to verify how a page
    actually looks/behaves.
    """
    cleaned_url = (url or "").strip()
    if not cleaned_url:
        return {"text": "ERROR: url is empty"}
    if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://")):
        return {"text": f"ERROR: url must start with http:// or https:// — got '{cleaned_url[:80]}'"}

    from app.application.web.ssrf_guard import check_ssrf
    reason = check_ssrf(cleaned_url)
    if reason:
        return {"text": f"ERROR: SSRF blocked — {reason}"}

    limit = max(500, min(int(max_chars), 50000))
    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            title, final_url, text = ex.submit(_browser_render, cleaned_url, wait_selector, limit).result(timeout=50)
    except Exception as exc:
        return {"text": f"ERROR: browser failed: {str(exc)[:300]}"}

    text = (text or "").strip()
    if not text:
        return {"text": f"[browser: {final_url}] страница отрендерилась, но видимого текста нет"}
    return {"text": f"[browser: {final_url}]\nTITLE: {title}\n\n{text}"}
