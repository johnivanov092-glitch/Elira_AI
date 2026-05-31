from __future__ import annotations

from typing import Any


class ProjectMapService:
    def build(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "stub"}

    def build_map(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.build(*args, **kwargs)

    def get_map(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "stub"}

    def search(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "stub"}
