from __future__ import annotations

from typing import Any

from app.services.project_brain_service import ProjectBrainService
from app.services.project_map_service import ProjectMapService


class ProjectBrainLoopService:
    """
    Bounded Project Brain loop.

    Default behavior is safe:
    - analyze only
    - no auto push unless explicitly enabled
    - bounded by max_iterations <= 3
    """

    def __init__(self) -> None:
        self.brain = ProjectBrainService()
        self.map_service = ProjectMapService()

    def analyze(self, focus: str = "backend", max_iterations: int = 3) -> dict[str, Any]:
        project_map = self.map_service.build_map()
        search = self.brain.find_code(focus)
        iterations: list[dict[str, Any]] = []

        hits = ((search.get("results") or {}).get("hits") or []) if isinstance(search, dict) else []
        candidate_paths: list[str] = []
        for item in hits[:max_iterations]:
            if isinstance(item, dict) and item.get("path"):
                candidate_paths.append(item["path"])

        if not candidate_paths:
            candidate_paths = [
                "backend/app/services/agents_service.py",
                "backend/app/services/planner_v2_service.py",
                "backend/app/services/tool_service.py",
            ]

        candidate_paths = candidate_paths[: max(1, min(int(max_iterations), 3))]

        for idx, path in enumerate(candidate_paths, start=1):
            file_info = self.brain.read_file(path)
            content = str(file_info.get("content", "")) if isinstance(file_info, dict) else ""
            suggestion = self._suggest_for_path(path, content)
            iterations.append(
                {
                    "iteration": idx,
                    "path": path,
                    "analysis": suggestion,
                    "can_patch": True,
                    "auto_pushed": False,
                }
            )

        return {
            "ok": True,
            "mode": "analyze",
            "focus": focus,
            "max_iterations": max_iterations,
            "project_map": project_map,
            "search": search,
            "iterations": iterations,
            "summary": {
                "count": len(iterations),
                "auto_push": False,
            },
        }

    def run_loop(
        self,
        *,
        path: str,
        new_content: str,
        message: str = "AI Project Brain patch",
        max_iterations: int = 1,
        auto_push: bool = False,
    ) -> dict[str, Any]:
        iterations: list[dict[str, Any]] = []
        loop_count = max(1, min(int(max_iterations), 3))

        for idx in range(1, loop_count + 1):
            result = self.brain.apply_patch_and_push(
                path=path,
                new_content=new_content,
                message=message,
                auto_push=auto_push,
            )
            iterations.append(
                {
                    "iteration": idx,
                    "path": path,
                    "result": result,
                    "auto_pushed": auto_push,
                }
            )
            break

        return {
            "ok": True,
            "mode": "apply_loop",
            "iterations": iterations,
            "summary": {
                "count": len(iterations),
                "auto_push": auto_push,
                "bounded": True,
                "max_iterations": loop_count,
            },
        }

    def _suggest_for_path(self, path: str, content: str) -> dict[str, Any]:
        text = (content or "").lower()

        suggestions: list[str] = []
        if "run_tool(" in text and path.endswith("agents_service.py"):
            suggestions.append("Вынести повторяющиеся вызовы tool-логики в helper-функции.")
        if "timeline" in text:
            suggestions.append("Упростить формирование timeline через единый helper.")
        if "route" in text and "tools" in text:
            suggestions.append("Разделить planner-логику и runtime-логику.")
        if "project_patch" in text:
            suggestions.append("Сделать dry-run режим по умолчанию перед apply.")
        if not suggestions:
            suggestions.append("Провести локальный рефакторинг и сократить ответственность файла.")

        return {
            "path": path,
            "suggestions": suggestions,
            "risk": "medium",
        }
