"""Project map service — builds and queries a project structure map."""
from __future__ import annotations

from typing import Any


class ProjectMapService:
    """Builds a high-level structural map of the open project and lets callers search it.

    Delegates to the infrastructure-level project_service helpers so the tool
    registry has a single stable class to instantiate.
    """

    def build_map(self, max_depth: int = 4, max_items: int = 500) -> dict[str, Any]:
        """Return a tree dict describing the project file structure."""
        try:
            from app.infrastructure.files.project_service import list_project_tree
            tree = list_project_tree(max_depth=max_depth, max_items=max_items)
            return {"ok": True, "tree": tree}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def search(self, query: str, max_hits: int = 30) -> dict[str, Any]:
        """Search project files by content and return matching hits."""
        try:
            from app.infrastructure.files.project_service import search_project
            hits = search_project(query, max_hits=max_hits)
            return {"ok": True, "query": query, "hits": hits, "count": len(hits) if isinstance(hits, list) else 0}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # Legacy stubs kept for backward compatibility
    def build(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.build_map()

    def get_map(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.build_map()
