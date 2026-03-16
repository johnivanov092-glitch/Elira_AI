from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from app.services.project_service import BASE_DIR, read_project_file, write_project_file


class ProjectPatchService:
    """
    Backward-compatible patch service with rollback snapshots.

    Existing methods stay intact:
    - preview_patch
    - apply_patch
    - replace_in_file

    New capabilities:
    - backup snapshots before apply
    - hash verification
    - rollback by backup id
    - backup listing
    """

    def __init__(self) -> None:
        self.project_root = BASE_DIR
        self.backup_dir = self.project_root / "data" / "patch_backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

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
        old_hash = self._hash_text(old_text)
        new_hash = self._hash_text(new_text)

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
            "old_hash": old_hash,
            "new_hash": new_hash,
            "old_content": old_text,
            "new_content": new_text,
        }

    def apply_patch(self, path: str, new_content: str) -> dict[str, Any]:
        preview = self.preview_patch(path, new_content, max_chars=500000)
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

        backup = self._create_backup(path, str(preview.get("old_content", "")))
        if not backup.get("ok"):
            return backup

        result = write_project_file(path, new_content)
        if not result.get("ok"):
            return {
                "ok": False,
                "path": path,
                "changed": False,
                "message": result.get("error", "Write failed"),
                "backup": backup,
            }

        verify = read_project_file(path, max_chars=500000)
        actual = str(verify.get("content", "")) if verify.get("ok") else ""
        actual_hash = self._hash_text(actual)

        if actual_hash != preview.get("new_hash"):
            rollback = self.rollback_patch(path, str(backup.get("backup_id", "")))
            return {
                "ok": False,
                "path": path,
                "changed": False,
                "message": "Hash verification failed; rollback executed",
                "expected_hash": preview.get("new_hash"),
                "actual_hash": actual_hash,
                "backup": backup,
                "rollback": rollback,
            }

        return {
            "ok": True,
            "path": path,
            "changed": True,
            "message": "Patch applied safely",
            "diff": preview.get("diff", ""),
            "backup": backup,
            "verification": {
                "ok": True,
                "new_hash": preview.get("new_hash"),
                "actual_hash": actual_hash,
            },
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

    def apply_replace_in_file(
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
        return self.apply_patch(path, updated)

    def list_backups(self, path: str | None = None, limit: int = 20) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for meta_path in sorted(self.backup_dir.glob("*.json"), reverse=True):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if path and meta.get("path") != path:
                continue
            items.append(meta)
            if len(items) >= limit:
                break
        return {"ok": True, "items": items, "count": len(items)}

    def rollback_patch(self, path: str, backup_id: str) -> dict[str, Any]:
        meta_path = self.backup_dir / f"{backup_id}.json"
        content_path = self.backup_dir / f"{backup_id}.bak"

        if not meta_path.exists() or not content_path.exists():
            return {
                "ok": False,
                "path": path,
                "backup_id": backup_id,
                "error": "Backup not found",
            }

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            original_content = content_path.read_text(encoding="utf-8")
        except Exception as exc:
            return {
                "ok": False,
                "path": path,
                "backup_id": backup_id,
                "error": str(exc),
            }

        if meta.get("path") != path:
            return {
                "ok": False,
                "path": path,
                "backup_id": backup_id,
                "error": "Backup path mismatch",
            }

        result = write_project_file(path, original_content)
        return {
            "ok": bool(result.get("ok")),
            "path": path,
            "backup_id": backup_id,
            "message": "Rollback applied" if result.get("ok") else result.get("error", "Rollback failed"),
            "write_result": result,
        }

    def _create_backup(self, path: str, old_content: str) -> dict[str, Any]:
        backup_id = f"{int(time.time() * 1000)}_{self._safe_name(path)}"
        backup_meta = {
            "backup_id": backup_id,
            "path": path,
            "created_at": time.time(),
            "old_hash": self._hash_text(old_content),
        }
        try:
            (self.backup_dir / f"{backup_id}.bak").write_text(old_content, encoding="utf-8")
            (self.backup_dir / f"{backup_id}.json").write_text(
                json.dumps(backup_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return {"ok": True, **backup_meta}
        except Exception as exc:
            return {"ok": False, "path": path, "error": str(exc)}

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    def _safe_name(self, path: str) -> str:
        return path.replace("/", "_").replace("\\", "_").replace(":", "_")
