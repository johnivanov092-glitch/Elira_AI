"""Stub: browser_agent — headless browser automation."""


class BrowserAgent:
    """Stub implementation returned when no real browser backend is configured."""

    def search(self, query: str = "", max_results: int = 5, **kw) -> dict:
        return {"ok": False, "error": "Browser search not available in this environment", "results": []}

    def run(self, start_url: str = "", steps: list | None = None, headless: bool = True, **kw) -> dict:
        return {"ok": False, "error": "Browser automation not available in this environment"}

    def screenshot(self, url: str = "", **kw) -> dict:
        return {"ok": False, "error": "Browser screenshot not available in this environment"}
