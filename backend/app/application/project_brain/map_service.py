from __future__ import annotations

from pathlib import Path
from typing import Any


def _open_project_root() -> Path | None:
    """The project the chat/IDE currently has open.

    The registry project-map tools have no per-call project argument, so they
    scope to the globally open project (set via ``advanced.runtime.open_project``)
    — the same root the read-only file tools use. Returns ``None`` when no
    project is open.
    """
    try:
        from app.application.advanced.runtime import _project_root
    except Exception:
        return None
    return _project_root()


class ProjectMapService:
    """Thin adapter onto the real, native code-agent map/recall tools.

    No second indexing path lives here: ``build_map`` delegates to
    :func:`app.application.code_agent.tools.tool_project_map` (read-only,
    bounded, stdlib-only) and ``search`` delegates to
    :func:`tool_recall` (RAG memory). Both are scoped to the currently open
    project. The native ``{"text": ...}`` shape is normalised to the registry
    ``{"ok": True, ...}`` convention.
    """

    def build(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.build_map(*args, **kwargs)

    def build_map(
        self,
        *,
        max_depth: int = 4,
        max_items: int = 500,
        **_: Any,
    ) -> dict[str, Any]:
        root = _open_project_root()
        if root is None:
            return {"ok": False, "error": "No project is open"}
        try:
            from app.application.code_agent.tools import tool_project_map
        except Exception as exc:  # pragma: no cover - import path
            return {"ok": False, "error": f"project map unavailable: {exc}"}
        try:
            result = tool_project_map(root, max_depth=int(max_depth), max_files=int(max_items))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "text": result.get("text", "")}

    def get_map(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.build_map(*args, **kwargs)

    def search(
        self,
        query: str = "",
        *,
        max_hits: int = 30,
        **_: Any,
    ) -> dict[str, Any]:
        cleaned = str(query or "").strip()
        if not cleaned:
            return {"ok": False, "error": "query is empty"}
        root = _open_project_root()
        if root is None:
            return {"ok": False, "error": "No project is open"}
        try:
            from app.application.code_agent.tools import tool_recall
        except Exception as exc:  # pragma: no cover - import path
            return {"ok": False, "error": f"recall unavailable: {exc}"}
        try:
            result = tool_recall(root, query=cleaned, top_k=max(1, int(max_hits)))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "text": result.get("text", "")}
