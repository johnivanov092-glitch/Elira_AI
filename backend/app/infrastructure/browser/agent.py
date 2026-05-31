from __future__ import annotations

from typing import Any


class BrowserAgent:
    def search(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "browser stub"}

    def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "browser stub"}

    def screenshot(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "browser stub"}
