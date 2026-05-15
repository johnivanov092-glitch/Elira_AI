from __future__ import annotations

from typing import Any

from app.services.project_service import list_project_tree, search_project, read_project_file
from app.services.project_patch_service import ProjectPatchService
from app.services.git_service import GitService


class ProjectBrainService:
    """
    Project Brain service without circular imports.

    Safe:
    - scans project
    - searches code
    - reads files
    - previews/applies patches
    - optionally commits/pushes via GitService

    IMPORTANT:
    This service does NOT import tool_service.
    """

    def scan_project(self) -> dict[str, Any]:
        tree = list_project_tree(max_depth=4, max_items=500)
        return {
            "ok": True,
            "type": "project_scan",
            "tree": tree,
        }

    def find_code(self, query: str) -> dict[str, Any]:
        results = search_project(query=query, max_hits=50)
        return {
            "ok": True,
            "type": "search",
            "query": query,
            "results": results,
        }

    def read_file(self, path: str) -> dict[str, Any]:
        return read_project_file(path, max_chars=20000)

    def preview_patch(self, path: str, new_content: str) -> dict[str, Any]:
        patch = ProjectPatchService()
        return patch.preview_patch(path, new_content, max_chars=20000)

    def apply_patch(self, path: str, new_content: str) -> dict[str, Any]:
        patch = ProjectPatchService()
        return patch.apply_patch(path, new_content)

    def apply_patch_and_push(
        self,
        path: str,
        new_content: str,
        message: str = "AI Project Brain patch",
        auto_push: bool = False,
    ) -> dict[str, Any]:
        preview = self.preview_patch(path, new_content)
        if not preview.get("ok"):
            return preview

        apply_result = self.apply_patch(path, new_content)
        if not apply_result.get("ok"):
            return {
                "ok": False,
                "preview": preview,
                "apply": apply_result,
                "git": None,
            }

        git_result = None
        if auto_push:
            git = GitService()
            git_result = git.commit_and_push(message)

        return {
            "ok": True,
            "preview": preview,
            "apply": apply_result,
            "git": git_result,
            "auto_push": auto_push,
        }
