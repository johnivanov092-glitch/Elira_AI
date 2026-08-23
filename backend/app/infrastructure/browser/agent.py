"""Headless-browser agent backing the registry ``browser_*`` tools.

Three capabilities, all read-oriented and SSRF-guarded:

* :meth:`BrowserAgent.search` — structured web search. It reuses the existing
  web-search backend (:func:`app.infrastructure.search.web_search.search_web`)
  rather than scraping a search engine's HTML, so there is no second search
  runtime here.
* :meth:`BrowserAgent.run` — drive a headless Chromium through a structured
  step language (``goto`` / ``click`` / ``fill`` / ``wait`` /
  ``extract`` / ``screenshot``) and return what each step produced. This is the
  genuinely browser-specific capability the registry exposes as ``browser_run``.
* :meth:`BrowserAgent.screenshot` — render a single URL to a base64 PNG.

Playwright is an *optional* dependency (``backend/requirements-optional.txt``).
When it (or the bundled Chromium) is unavailable, the browser-driving methods
degrade to a structured ``{"ok": False, "error": ...}`` instead of raising — the
registry handler contract is "always return a dict".

Every URL the browser navigates to is shape-validated with
:func:`app.application.web.ssrf_guard.check_ssrf`; loopback, private LAN,
link-local and metadata destinations are permitted.
"""
from __future__ import annotations

import base64
from typing import Any

from app.application.web.ssrf_guard import check_ssrf

# Step actions the run() loop understands. Anything else is rejected so the
# agent cannot be driven into arbitrary Playwright API calls.
_ALLOWED_STEPS = frozenset(
    {"goto", "click", "fill", "type", "wait", "wait_for", "extract", "screenshot"}
)

_DEFAULT_NAV_TIMEOUT_MS = 20000
_MAX_EXTRACT_CHARS = 20000


def _playwright_unavailable() -> str | None:
    """Return a human-readable reason if Playwright can't be used, else None."""
    try:
        import playwright.sync_api  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on optional install
        return (
            "Playwright is not installed — add it via "
            "`pip install -r backend/requirements-optional.txt` and run "
            f"`python -m playwright install chromium` ({exc})"
        )
    return None


class BrowserAgent:
    """Real headless-browser agent (Playwright/Chromium) with graceful fallback."""

    def search(self, query: str = "", *, max_results: int = 5, **_: Any) -> dict[str, Any]:
        """Structured web search, reusing the shared web-search backend."""
        cleaned = str(query or "").strip()
        if not cleaned:
            return {"ok": False, "error": "query is empty"}
        try:
            from app.infrastructure.search.web_search import search_web
        except Exception as exc:  # pragma: no cover - import path
            return {"ok": False, "error": f"web search unavailable: {exc}"}
        try:
            result = search_web(cleaned, max_results=max(1, int(max_results)))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        # search_web already returns {"ok", "query", "sources", "context", ...}.
        if isinstance(result, dict):
            return result
        return {"ok": True, "query": cleaned, "results": result}

    def run(
        self,
        start_url: str = "",
        *,
        steps: list[dict[str, Any]] | None = None,
        headless: bool = True,
        **_: Any,
    ) -> dict[str, Any]:
        """Drive a headless browser through *steps*, starting at *start_url*."""
        url = str(start_url or "").strip()
        if not url:
            return {"ok": False, "error": "start_url is empty"}
        ssrf_reason = check_ssrf(url)
        if ssrf_reason:
            return {"ok": False, "error": f"Invalid URL — {ssrf_reason}"}

        raw_steps = steps if isinstance(steps, list) else []
        unavailable = _playwright_unavailable()
        if unavailable:
            return {"ok": False, "error": unavailable}

        from playwright.sync_api import sync_playwright

        outputs: list[dict[str, Any]] = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=bool(headless))
                try:
                    page = browser.new_page()
                    page.set_default_timeout(_DEFAULT_NAV_TIMEOUT_MS)
                    page.goto(url, wait_until="domcontentloaded")
                    outputs.append({"step": "goto", "url": url, "ok": True})

                    for index, step in enumerate(raw_steps):
                        if not isinstance(step, dict):
                            outputs.append({"index": index, "ok": False, "error": "step must be an object"})
                            continue
                        action = str(step.get("action", "")).strip().lower()
                        if action not in _ALLOWED_STEPS:
                            outputs.append({"index": index, "ok": False, "error": f"unknown action: {action!r}"})
                            continue
                        outputs.append(self._run_step(page, index, action, step))

                    final_url = page.url
                finally:
                    browser.close()
        except Exception as exc:
            return {"ok": False, "error": str(exc), "steps": outputs}

        return {"ok": True, "start_url": url, "final_url": final_url, "steps": outputs}

    def screenshot(
        self,
        url: str = "",
        *,
        full_page: bool = True,
        headless: bool = True,
        **_: Any,
    ) -> dict[str, Any]:
        """Render *url* to a base64-encoded PNG."""
        target = str(url or "").strip()
        if not target:
            return {"ok": False, "error": "url is empty"}
        ssrf_reason = check_ssrf(target)
        if ssrf_reason:
            return {"ok": False, "error": f"Invalid URL — {ssrf_reason}"}

        unavailable = _playwright_unavailable()
        if unavailable:
            return {"ok": False, "error": unavailable}

        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=bool(headless))
                try:
                    page = browser.new_page()
                    page.set_default_timeout(_DEFAULT_NAV_TIMEOUT_MS)
                    page.goto(target, wait_until="domcontentloaded")
                    png = page.screenshot(full_page=bool(full_page))
                finally:
                    browser.close()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "url": target,
            "format": "png",
            "image_base64": base64.b64encode(png).decode("ascii"),
        }

    # ------------------------------------------------------------------ steps

    def _run_step(self, page: Any, index: int, action: str, step: dict[str, Any]) -> dict[str, Any]:
        """Execute a single whitelisted step against *page*."""
        try:
            if action == "goto":
                href = str(step.get("url", "")).strip()
                reason = check_ssrf(href)
                if reason:
                    return {"index": index, "action": action, "ok": False, "error": f"Invalid URL — {reason}"}
                page.goto(href, wait_until="domcontentloaded")
                return {"index": index, "action": action, "ok": True, "url": href}

            if action == "click":
                page.click(str(step.get("selector", "")))
                return {"index": index, "action": action, "ok": True, "selector": step.get("selector", "")}

            if action in ("fill", "type"):
                page.fill(str(step.get("selector", "")), str(step.get("value", step.get("text", ""))))
                return {"index": index, "action": action, "ok": True, "selector": step.get("selector", "")}

            if action in ("wait", "wait_for"):
                selector = str(step.get("selector", "")).strip()
                if selector:
                    page.wait_for_selector(selector)
                    return {"index": index, "action": action, "ok": True, "selector": selector}
                ms = int(step.get("ms", step.get("timeout", 1000)))
                page.wait_for_timeout(max(0, min(ms, 10000)))
                return {"index": index, "action": action, "ok": True, "ms": ms}

            if action == "extract":
                selector = str(step.get("selector", "")).strip()
                if selector:
                    locator = page.locator(selector)
                    text = "\n".join(locator.all_inner_texts())
                else:
                    text = page.inner_text("body")
                return {"index": index, "action": action, "ok": True, "text": text[:_MAX_EXTRACT_CHARS]}

            if action == "screenshot":
                png = page.screenshot(full_page=bool(step.get("full_page", False)))
                return {
                    "index": index,
                    "action": action,
                    "ok": True,
                    "format": "png",
                    "image_base64": base64.b64encode(png).decode("ascii"),
                }
        except Exception as exc:
            return {"index": index, "action": action, "ok": False, "error": str(exc)}

        return {"index": index, "action": action, "ok": False, "error": "unhandled action"}
