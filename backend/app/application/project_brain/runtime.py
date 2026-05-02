# -*- coding: utf-8 -*-
"""Application-layer runtime for Project Brain operations.

Aggregates project scanning, code search, file read, patch preview/apply,
and optional git commit.  Pure Python — no HTTP, no FastAPI imports.

Bug fix: the original ``services/project_brain_service.py`` imported a
non-existent ``GitService`` class from ``git_service``.  Replaced with the
function-based ``git_commit()`` API that actually exists in
``application/git/runtime.py``.
"""
from __future__ import annotations

from typing import Any


# ── public API ────────────────────────────────────────────────────────────────

def scan_project() -> dict[str, Any]:
    from app.services.project_service import list_project_tree
    tree = list_project_tree(max_depth=4, max_items=500)
    return {"ok": True, "type": "project_scan", "tree": tree}


def find_code(query: str) -> dict[str, Any]:
    from app.services.project_service import search_project
    results = search_project(query=query, max_hits=50)
    return {"ok": True, "type": "search", "query": query, "results": results}


def read_file(path: str) -> dict[str, Any]:
    from app.services.project_service import read_project_file
    return read_project_file(path, max_chars=20000)


def preview_patch(path: str, new_content: str) -> dict[str, Any]:
    from app.services.project_patch_service import ProjectPatchService
    patch = ProjectPatchService()
    return patch.preview_patch(path, new_content, max_chars=20000)


def apply_patch(path: str, new_content: str) -> dict[str, Any]:
    from app.services.project_patch_service import ProjectPatchService
    patch = ProjectPatchService()
    return patch.apply_patch(path, new_content)


def apply_patch_and_push(
    path: str,
    new_content: str,
    message: str = "AI Project Brain patch",
    auto_push: bool = False,
) -> dict[str, Any]:
    preview = preview_patch(path, new_content)
    if not preview.get("ok"):
        return preview

    apply_result = apply_patch(path, new_content)
    if not apply_result.get("ok"):
        return {"ok": False, "preview": preview, "apply": apply_result, "git": None}

    git_result = None
    if auto_push:
        from app.application.git.runtime import git_commit
        git_result = git_commit(message)

    return {
        "ok": True,
        "preview": preview,
        "apply": apply_result,
        "git": git_result,
        "auto_push": auto_push,
    }


class ProjectBrainService:
    """Backward-compatible class wrapper for the project brain functions."""

    def scan_project(self) -> dict[str, Any]:
        return scan_project()

    def find_code(self, query: str) -> dict[str, Any]:
        return find_code(query)

    def read_file(self, path: str) -> dict[str, Any]:
        return read_file(path)

    def preview_patch(self, path: str, new_content: str) -> dict[str, Any]:
        return preview_patch(path, new_content)

    def apply_patch(self, path: str, new_content: str) -> dict[str, Any]:
        return apply_patch(path, new_content)

    def apply_patch_and_push(
        self,
        path: str,
        new_content: str,
        message: str = "AI Project Brain patch",
        auto_push: bool = False,
    ) -> dict[str, Any]:
        return apply_patch_and_push(path, new_content, message, auto_push)
