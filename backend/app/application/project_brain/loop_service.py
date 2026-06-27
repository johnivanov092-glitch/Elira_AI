from __future__ import annotations

from typing import Any


class ProjectBrainLoopService:
    """Read-only project analysis + a guard for the legacy "dev loop" tool.

    ``analyze`` delegates to the real native project map (scoped to the open
    project) so the registry ``project_brain_analyze`` tool returns a genuine
    structural overview instead of a stub.

    ``run_loop`` has no honest in-handler implementation: an iterative
    edit/commit/push development loop is exactly what the unified code-agent
    ReAct runtime already provides, and re-implementing it here would mean a
    second executor (forbidden by docs/ARCHITECTURE.md → Runtime Guardrails).
    It therefore returns a clear, structured redirect rather than pretending to
    run.
    """

    def analyze(self, *, focus: str = "backend", max_iterations: int = 3, **_: Any) -> dict[str, Any]:
        from app.application.project_brain.map_service import ProjectMapService

        result = ProjectMapService().build_map(max_depth=4, max_items=600)
        if not result.get("ok"):
            return result
        return {"ok": True, "focus": str(focus or "backend"), "text": result.get("text", "")}

    def run_loop(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "error": (
                "project_brain_loop is not a standalone tool — run the code-agent "
                "(POST /api/code-agent/stream) to make iterative, approval-gated "
                "edits. This handler does not implement a second execution loop."
            ),
        }
