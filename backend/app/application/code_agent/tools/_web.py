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


def _resolve_locator(page, selector: str, *, kind: str):
    """Best-effort locator for an interaction step. Accepts a raw CSS selector, or a
    human label / button text / placeholder / input name — trying each strategy so the
    model can say `fill: "CIDR"` (a label) or `click: "Calculate"` (button text) without
    knowing the DOM. Returns a Playwright locator with ≥1 match, or None."""
    sel = (selector or "").strip()
    if not sel:
        return None
    looks_css = sel[0] in "#.[" or (" " not in sel and any(c in sel for c in "#.>[]="))
    strategies = []
    if looks_css:
        strategies.append(lambda: page.locator(sel))
    if kind == "check":
        strategies += [
            lambda: page.get_by_role("checkbox", name=sel),
            lambda: page.get_by_label(sel, exact=False),
            lambda: page.locator(f"input[type='checkbox'][name='{sel}'], #{sel}"),
        ]
    elif kind == "fill":
        strategies += [
            lambda: page.get_by_label(sel, exact=False),
            lambda: page.get_by_placeholder(sel),
            lambda: page.get_by_role("textbox", name=sel),
            lambda: page.locator(f"input[name='{sel}'], textarea[name='{sel}'], select[name='{sel}'], #{sel}"),
        ]
    else:  # click
        strategies += [
            lambda: page.get_by_role("button", name=sel),
            lambda: page.get_by_role("link", name=sel),
            lambda: page.get_by_text(sel, exact=False),
        ]
    if not looks_css:
        strategies.append(lambda: page.locator(sel))
    for make in strategies:
        try:
            loc = make().first
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def _apply_action(page, act: dict) -> bool:
    """Apply one interaction step before the DOM is captured:
      {"fill": <label|css>, "value": ...}    type into an input (value "" clears it)
      {"select": <label|css>, "value": ...}  choose a <select> option (by label/value/text)
      {"check": <label|css>} / {"uncheck": …} toggle a checkbox
      {"click": <text|css>}                   click a button/link
      {"wait": <ms>}                          pause
    fill/select accept a CSS selector OR a human label; best-effort and non-fatal — a bad
    step is skipped so a later assertion still reflects the REAL post-interaction DOM.
    Returns True only when a real INTERACTION (fill/select/check/click) resolved and ran —
    so the caller can tell an actual interaction from a no-op / plain render (a bare `wait`
    returns False). Each locator action is time-bounded so one miss can't stall the render."""
    if not isinstance(act, dict):
        return False
    try:
        if "fill" in act:
            loc = _resolve_locator(page, str(act.get("fill") or ""), kind="fill")
            if loc is not None:
                loc.fill(str(act.get("value", "") if act.get("value") is not None else ""), timeout=8000)
                return True
        elif "select" in act:
            loc = _resolve_locator(page, str(act.get("select") or ""), kind="fill")
            if loc is not None:
                opt = str(act.get("value", act.get("option", "")) or "")
                for kw in ("label", "value", None):
                    try:
                        if kw:
                            loc.select_option(**{kw: opt}, timeout=8000)
                        else:
                            loc.select_option(opt, timeout=8000)
                        return True
                    except Exception:
                        continue
        elif "check" in act or "uncheck" in act:
            want = "check" in act
            loc = _resolve_locator(page, str(act.get("check") or act.get("uncheck") or ""), kind="check")
            if loc is not None:
                (loc.check if want else loc.uncheck)(timeout=8000)
                return True
        elif "click" in act:
            loc = _resolve_locator(page, str(act.get("click") or ""), kind="click")
            if loc is not None:
                loc.click(timeout=8000)
                return True
        elif "wait" in act:
            page.wait_for_timeout(max(0, min(int(act.get("wait") or 500), 10000)))
    except Exception:
        return False
    return False


# Named viewport sizes (px) — a layout criterion ("no horizontal scroll on mobile") is
# only meaningfully tested at the width it names, so the model (steered by the closure
# hint) picks a preset and we measure horizontal overflow at THAT width.
_VIEWPORT_PRESETS = {
    "mobile": {"width": 375, "height": 812},
    "tablet": {"width": 768, "height": 1024},
    "desktop": {"width": 1280, "height": 800},
}


def _coerce_viewport(value: Any) -> dict | None:
    """Normalize the `viewport` arg → {'width':W,'height':H} or None. Accepts a preset
    name ('mobile'/'tablet'/'desktop') or an explicit {'width':…,'height':…}."""
    if isinstance(value, str):
        return _VIEWPORT_PRESETS.get(value.strip().lower())
    if isinstance(value, dict):
        try:
            w = int(value.get("width") or 0)
            h = int(value.get("height") or 0)
        except (TypeError, ValueError):
            return None
        if w >= 200 and h >= 200:
            return {"width": min(w, 4096), "height": min(h, 4096)}
    return None


def _browser_render(url: str, wait_selector: str | None, limit: int,
                    actions: list[dict] | None = None,
                    viewport: dict | None = None) -> tuple[str, str, str, int, dict | None]:
    """Render a page with Playwright, optionally performing interaction steps (fill /
    select / check / click / wait) before capturing the DOM — so an interaction criterion
    ("after typing X and clicking Calculate the DOM shows Network: …") is verified against
    the ACTUAL post-interaction DOM, not a static render. When `viewport` is given, the page
    is sized to it and horizontal overflow is measured (positive layout evidence). Returns
    (title, url, body, applied, viewport_signal) where `applied` counts real interactions
    that resolved+ran and `viewport_signal` is {'checked':True,'width':W,'no_hoverflow':bool}
    (or None when no viewport was requested). Runs in a worker thread (see tool_browser):
    the Playwright sync API must not be called from inside an asyncio loop."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport=viewport) if viewport else browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass
            applied = 0
            if actions:
                for act in actions:
                    if _apply_action(page, act):
                        applied += 1
                page.wait_for_timeout(300)  # let the DOM settle after interactions
            vp_signal = None
            if viewport:
                try:
                    # No horizontal overflow at the tested width = layout fits (real signal,
                    # not "a render happened"). +1 tolerates sub-pixel rounding.
                    fits = bool(page.evaluate(
                        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"))
                    inner = int(page.evaluate("() => window.innerWidth") or viewport["width"])
                    vp_signal = {"checked": True, "width": inner, "no_hoverflow": fits}
                except Exception:
                    vp_signal = None   # measurement failed → no viewport verdict (honest)
            return page.title(), page.url, (page.inner_text("body") or "")[:limit], applied, vp_signal
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
            _title, _final_url, text, _applied, _vp = ex.submit(_browser_render, url, None, limit).result(timeout=45)
        return (text or "").strip()
    except Exception:
        return ""


def tool_browser(*, url: str, wait_selector: str | None = None, max_chars: int = 8000,
                 actions: list[dict] | None = None, viewport: Any = None) -> dict[str, Any]:
    """Open a URL in a real headless browser (Playwright/Chromium), render
    JavaScript, optionally perform interaction steps, and return the visible page
    text. Use when `web_fetch` is not enough — pages that need JS to render, SPAs,
    or to verify how a page actually looks/behaves.

    `actions` drives real interaction in ONE session so an "after clicking Calculate
    the DOM shows Network: …" criterion is verified against the post-interaction DOM:
      actions=[{"fill": "CIDR", "value": "192.168.1.0/24"}, {"click": "Calculate"}]
    fill/click accept a CSS selector OR a human label / button text.

    `viewport` sizes the page and measures horizontal overflow — use it to verify a
    layout / responsive criterion ("no horizontal scroll on mobile"): pass a preset
    ("mobile"/"tablet"/"desktop") or {"width":375,"height":812}. The result carries a
    `viewport` signal {checked,width,no_hoverflow} used by the layout verifier.
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

    steps = actions if isinstance(actions, list) else None
    vp = _coerce_viewport(viewport)
    limit = max(500, min(int(max_chars), 50000))
    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            title, final_url, text, applied, vp_signal = ex.submit(
                _browser_render, cleaned_url, wait_selector, limit, steps, vp
            ).result(timeout=60 if (steps or vp) else 50)
    except Exception as exc:
        return {"text": f"ERROR: browser failed: {str(exc)[:300]}", "ok": False}

    text = (text or "").strip()
    if not text:
        return {"text": f"[browser: {final_url}] страница отрендерилась, но видимого текста нет", "ok": False}
    # A real render IS a verdict: the page LOADED (page_open) and the returned DOM text is
    # genuine visible-text evidence — unlike a bundle grep. `interacted` = a real fill/
    # select/check/click actually resolved and ran, so an interaction criterion can require
    # the actions to have happened (not a plain render). The action summary goes ONLY in the
    # human `text` field — NEVER in `evidence`, so a criterion token can't match the echoed
    # fill value instead of the real rendered DOM.
    interacted = bool(steps) and applied >= 1
    act_note = ""
    if steps:
        done = "; ".join(
            (f"fill {a.get('fill')}={a.get('value')}" if "fill" in a else
             f"select {a.get('select')}={a.get('value')}" if "select" in a else
             f"check {a.get('check')}" if "check" in a else
             f"uncheck {a.get('uncheck')}" if "uncheck" in a else
             f"click {a.get('click')}" if "click" in a else f"wait {a.get('wait')}")
            for a in steps if isinstance(a, dict)
        )
        act_note = f"[после действий ({applied}): {done}]\n"
    vp_note = ""
    if vp_signal:
        fit = "нет горизонтального переполнения" if vp_signal["no_hoverflow"] else "ЕСТЬ горизонтальный скролл"
        vp_note = f"[viewport {vp_signal['width']}px: {fit}]\n"
    return {
        "text": f"[browser: {final_url}]\n{vp_note}{act_note}TITLE: {title}\n\n{text}",
        "ok": True,
        "verifier": True,
        "evidence": (f"TITLE: {title}\n{text}")[:8000],   # DOM only — no action echo
        "interacted": interacted,
        "viewport": vp_signal,   # {checked,width,no_hoverflow} or None — drives layout verdict
    }
