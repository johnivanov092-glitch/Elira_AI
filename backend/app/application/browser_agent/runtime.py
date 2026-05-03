"""Stub: browser_agent — placeholder for headless browser automation.

When a real Playwright-based BrowserAgent is implemented, it will live here.
Currently returns stub errors to indicate the capability is not yet available.
"""
from __future__ import annotations
from typing import Any


class BrowserAgent:
    def search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        return {"ok": False, "error": "browser_agent stub — not yet implemented"}

    def run(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return {"ok": False, "error": "browser_agent stub — not yet implemented"}

    def screenshot(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return {"ok": False, "error": "browser_agent stub — not yet implemented"}
