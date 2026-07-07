from __future__ import annotations

from typing import Any

# Phase A — JS auto-render: static (BeautifulSoup) extraction returns little/no
# text for SPA / JS-rendered pages (currency tickers, dashboards). Below this many
# characters web_fetch transparently retries with the headless browser and keeps
# the rendered text only if it is actually richer. Fail-open.
_THIN_TEXT_THRESHOLD = 200

# Batch web tools: the model can pass multiple queries/urls in ONE call and we
# fan them out concurrently (I/O-bound → bounded thread pool). Caps keep upstream
# engines/sites from being hammered.
_WEB_BATCH_MAX = 6        # max queries / urls accepted per call
_WEB_BATCH_WORKERS = 5    # max concurrent requests


# SearXNG engine categories the agent may target (passed only to SearXNG; other
# engines ignore them). Keep in sync with the tool schema enum.
_WEB_SEARCH_CATEGORIES = frozenset(
    {"general", "news", "it", "science", "images", "videos", "map", "music", "files"}
)
_WEB_SEARCH_TIME_RANGES = frozenset({"day", "week", "month", "year"})


def _coerce_str_list(value: Any) -> list[str]:
    """Normalize a tool arg that should be a list of non-empty strings."""
    if isinstance(value, list):
        return [s for s in (str(x).strip() for x in value) if s]
    return []


def _run_search(query: str, limit: int, cat: str, tr: str) -> list[dict]:
    """Run one query through the web stack; return its source dicts (or [])."""
    from app.infrastructure.search.web_search import search_web
    try:
        result = search_web(query, max_results=limit, categories=cat or None, time_range=tr or None)
    except Exception:
        return []
    return result.get("sources") or []


def _format_search_results(sources: list[dict], header: str, limit: int) -> str:
    lines = [header]
    for i, item in enumerate(sources[:limit], 1):
        title = (item.get("title") or "").strip() or "(no title)"
        # Engine results carry the link under "href" (SearXNG/DDG/Wikipedia);
        # fall back to "url" for any source that uses that key.
        url = (item.get("href") or item.get("url") or "").strip()
        snippet = (item.get("body") or item.get("snippet") or item.get("content") or "").strip()
        if len(snippet) > 350:
            snippet = snippet[:350] + " […]"
        lines.append(f"\n[{i}] {title}\n    {url}\n    {snippet}" if snippet else f"\n[{i}] {title}\n    {url}")
    return "\n".join(lines)


def tool_web_search(
    *,
    query: str = "",
    queries: Any = None,
    top_k: int = 5,
    categories: str = "",
    time_range: str = "",
) -> dict[str, Any]:
    """Search the web (SearXNG / DuckDuckGo / Wikipedia). Returns ranked results
    with title + URL + snippet. Use `web_fetch` after to read a result in full.

    Pass `queries` (a list of strings) to run SEVERAL searches in PARALLEL in one
    call — much faster than issuing them one by one; results are merged and
    de-duplicated. Otherwise pass a single `query`.

    Optional targeting (via SearXNG): `categories` ("it"=github/stackoverflow/
    pypi/mdn, "science"=arxiv/pubmed/scholar, "news", "map", "images", "videos")
    and `time_range` ("day"|"week"|"month"|"year") for recency.
    """
    cat = (categories or "").strip().lower()
    cat = cat if cat in _WEB_SEARCH_CATEGORIES else ""
    tr = (time_range or "").strip().lower()
    tr = tr if tr in _WEB_SEARCH_TIME_RANGES else ""
    limit = max(1, min(int(top_k), 10))
    focus = "".join(f" · {x}" for x in (cat, tr) if x)

    query_list = _coerce_str_list(queries)
    if query_list:
        # ── Batch: several queries in parallel, merged + de-duped by URL ──────
        query_list = query_list[:_WEB_BATCH_MAX]
        import concurrent.futures
        per_query: dict[str, list[dict]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(query_list), _WEB_BATCH_WORKERS)) as ex:
            futs = {ex.submit(_run_search, q, limit, cat, tr): q for q in query_list}
            for f in concurrent.futures.as_completed(futs):
                per_query[futs[f]] = f.result() if not f.exception() else []
        seen: set[str] = set()
        merged: list[dict] = []
        for q in query_list:  # preserve query order for stable output
            for item in per_query.get(q, []):
                u = (item.get("href") or item.get("url") or "").strip()
                if u and u not in seen:
                    seen.add(u)
                    merged.append(item)
        if not merged:
            return {"text": f"No web results for {len(query_list)} queries."}
        header = f"Found {len(merged)} results across {len(query_list)} parallel queries{focus}:"
        return {"text": _format_search_results(merged, header, _WEB_BATCH_MAX * limit)}

    # ── Single query (back-compat) ───────────────────────────────────────────
    cleaned = (query or "").strip()
    if not cleaned:
        return {"text": "ERROR: query is empty (pass `query` or `queries`)"}
    try:
        from app.infrastructure.search.web_search import search_web
    except Exception as exc:  # pragma: no cover - import path
        return {"text": f"ERROR: web search unavailable: {exc}"}
    result = search_web(cleaned, max_results=limit, categories=cat or None, time_range=tr or None)
    sources = result.get("sources") or []
    if not sources:
        return {"text": f"No web results for '{cleaned}'"}
    engines = ", ".join(result.get("engines_used") or []) or "?"
    return {"text": _format_search_results(sources, f"Found {len(sources)} results via {engines}{focus}:", limit)}


def _fetch_one(url: str, limit: int) -> str:
    """Fetch + extract one page (static, with the Phase A JS-render fallback).
    Returns a formatted text block or an ERROR string. Never raises."""
    cleaned_url = (url or "").strip()
    if not cleaned_url:
        return "ERROR: url is empty"
    if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://")):
        return f"ERROR: url must start with http:// or https:// — got '{cleaned_url[:80]}'"

    from app.application.web.ssrf_guard import check_ssrf
    ssrf_reason = check_ssrf(cleaned_url)
    if ssrf_reason:
        return f"ERROR: SSRF blocked — {ssrf_reason}"

    try:
        from app.infrastructure.search.web_search import fetch_page_text
    except Exception as exc:  # pragma: no cover
        return f"ERROR: web fetch unavailable: {exc}"

    try:
        body = fetch_page_text(cleaned_url, max_chars=limit)
    except Exception as exc:
        return f"ERROR: {exc}"

    body = (body or "").strip()
    # Phase A — transparent JS auto-render. Static extraction misses JS-rendered
    # content, so on a thin/empty result retry with the headless browser and use
    # it when it actually yields more text. Fail-open: keep the static body on any
    # render failure (incl. Playwright absent).
    if len(body) < _THIN_TEXT_THRESHOLD:
        rendered = _render_fallback(cleaned_url, limit)
        if len(rendered) > len(body):
            return f"[fetched: {cleaned_url} · отрисовано в браузере (JS)]\n\n{rendered}"
    if not body:
        return f"ERROR: empty or non-HTML response from {cleaned_url}"
    return f"[fetched: {cleaned_url}]\n\n{body}"


def tool_web_fetch(*, url: str = "", urls: Any = None, max_chars: int = 8000) -> dict[str, Any]:
    """Fetch a web page and extract its main readable text (nav/ads/scripts
    stripped). JS-rendered pages (SPA/dashboards/tickers) are transparently
    re-fetched with a headless browser when static extraction is thin.

    Pass `urls` (a list) to fetch SEVERAL pages in PARALLEL in one call — far
    faster than fetching them one by one. Otherwise pass a single `url`.
    Use AFTER `web_search` has surfaced URLs worth reading in full.
    """
    limit = max(500, min(int(max_chars), 50000))

    url_list = _coerce_str_list(urls)
    if url_list:
        # ── Batch: fetch several pages in parallel ───────────────────────────
        url_list = url_list[:_WEB_BATCH_MAX]
        import concurrent.futures
        blocks: dict[int, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(url_list), _WEB_BATCH_WORKERS)) as ex:
            futs = {ex.submit(_fetch_one, u, limit): i for i, u in enumerate(url_list)}
            for f in concurrent.futures.as_completed(futs):
                i = futs[f]
                blocks[i] = f.result() if not f.exception() else f"ERROR: {f.exception()}"
        ordered = [blocks[i] for i in range(len(url_list))]
        return {"text": f"Fetched {len(url_list)} pages in parallel:\n\n" + "\n\n———\n\n".join(ordered)}

    # ── Single page (back-compat) ────────────────────────────────────────────
    return {"text": _fetch_one(url, limit)}


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


def _render_fallback(url: str, limit: int) -> str:
    """Best-effort headless-browser render for a thin/empty static fetch (Phase A).
    Reuses _browser_render in a worker thread (the Playwright sync API must not be
    called from inside an asyncio loop). Returns '' on ANY failure — Playwright not
    installed, navigation/timeout error — so web_fetch fails open to the static body.
    """
    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            _title, _final_url, text = ex.submit(_browser_render, url, None, limit).result(timeout=45)
        return (text or "").strip()
    except Exception:
        return ""


def tool_browser(*, url: str, wait_selector: str | None = None, max_chars: int = 8000) -> dict[str, Any]:
    """Open a URL in a real headless browser (Playwright/Chromium), render
    JavaScript, and return the visible page text. Use when `web_fetch` is not
    enough — pages that need JS to render, SPAs, or to verify how a page
    actually looks/behaves.
    """
    cleaned_url = (url or "").strip()
    # ERROR branches return ok=False WITHOUT a verifier flag: the render couldn't
    # run, so a matched page_open/text_visible criterion stays unconfirmed (not failed).
    if not cleaned_url:
        return {"text": "ERROR: url is empty", "ok": False}
    if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://")):
        return {"text": f"ERROR: url must start with http:// or https:// — got '{cleaned_url[:80]}'", "ok": False}

    from app.application.code_agent.tools._run import active_server_ports
    from app.application.web.ssrf_guard import check_ssrf
    reason = check_ssrf(cleaned_url, allow_loopback_ports=active_server_ports())
    if reason:
        return {"text": f"ERROR: SSRF blocked — {reason}", "ok": False}

    limit = max(500, min(int(max_chars), 50000))
    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            title, final_url, text = ex.submit(_browser_render, cleaned_url, wait_selector, limit).result(timeout=50)
    except Exception as exc:
        return {"text": f"ERROR: browser failed: {str(exc)[:300]}", "ok": False}

    text = (text or "").strip()
    if not text:
        return {"text": f"[browser: {final_url}] страница отрендерилась, но видимого текста нет", "ok": False}
    # A real render IS a verdict: the page LOADED (page_open) and the returned DOM
    # text is genuine visible-text evidence (text_visible) — unlike a bundle grep.
    # The evidence carries the rendered text so the criteria matcher can check which
    # named tokens (`VaultDesk`, `Start local audit`) are actually on the page.
    rendered = f"TITLE: {title}\n{text}"
    return {
        "text": f"[browser: {final_url}]\nTITLE: {title}\n\n{text}",
        "ok": True,
        "verifier": True,
        "evidence": rendered[:8000],
    }
