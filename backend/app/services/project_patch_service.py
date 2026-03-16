from __future__ import annotations

import difflib
from typing import Any

from app.services.project_service import read_project_file, write_project_file


class ProjectPatchService:
    """
    Stage 5 project patch helper.

    Responsibilities:
    - preview unified diff
    - apply full file replacement
    - apply simple string replacement patch
    """

    def preview_patch(self, path: str, new_content: str, max_chars: int = 20000) -> dict[str, Any]:
        current = read_project_file(path, max_chars=max_chars)
        if not current.get("ok"):
            return {
                "ok": False,
                "path": path,
                "error": current.get("error", "Cannot read file"),
            }

        old_text = str(current.get("content", ""))
        new_text = str(new_content or "")

        diff = "\n".join(
            difflib.unified_diff(
                old_text.splitlines(),
                new_text.splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )

        return {
            "ok": True,
            "path": path,
            "changed": old_text != new_text,
            "diff": diff,
            "old_size": len(old_text),
            "new_size": len(new_text),
            "old_content": old_text,
            "new_content": new_text,
        }

    def apply_patch(self, path: str, new_content: str) -> dict[str, Any]:
        preview = self.preview_patch(path, new_content)
        if not preview.get("ok"):
            return preview

        if not preview.get("changed"):
            return {
                "ok": True,
                "path": path,
                "changed": False,
                "message": "No changes to apply",
                "diff": "",
            }

        result = write_project_file(path, new_content)
        return {
            "ok": bool(result.get("ok")),
            "path": path,
            "changed": True,
            "message": "Patch applied" if result.get("ok") else result.get("error", "Write failed"),
            "diff": preview.get("diff", ""),
            "write_result": result,
        }

    def replace_in_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        max_chars: int = 20000,
    ) -> dict[str, Any]:
        current = read_project_file(path, max_chars=max_chars)
        if not current.get("ok"):
            return {
                "ok": False,
                "path": path,
                "error": current.get("error", "Cannot read file"),
            }

        content = str(current.get("content", ""))
        if old_text not in content:
            return {
                "ok": False,
                "path": path,
                "error": "Old text not found in file",
            }

        updated = content.replace(old_text, new_text, 1)
        return self.preview_patch(path, updated, max_chars=max_chars)
