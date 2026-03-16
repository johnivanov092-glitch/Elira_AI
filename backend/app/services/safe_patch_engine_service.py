from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class SafePatchEngineService:
    def __init__(
        self,
        project_root: str = ".",
        backup_dir: str = "data/patch_backups",
        run_trace_service=None,
        event_bus=None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.backup_dir = Path(backup_dir).resolve()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.run_trace_service = run_trace_service
        self.event_bus = event_bus

    def preview_patch(self, file_path: str, new_content: str) -> dict:
        target = self.project_root / file_path
        old_content = self._read_text(target)
        diff = "\n".join(
            difflib.unified_diff(
                old_content.splitlines(),
                new_content.splitlines(),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
                lineterm="",
            )
        )
        return {
            "status": "ok",
            "file_path": file_path,
            "exists": target.exists(),
            "old_sha256": self._sha256_text(old_content),
            "new_sha256": self._sha256_text(new_content),
            "diff": diff,
        }

    def apply_patch(self, file_path: str, new_content: str, expected_old_sha256: Optional[str] = None) -> dict:
        target = self.project_root / file_path
        current_content = self._read_text(target)
        current_sha = self._sha256_text(current_content)

        if expected_old_sha256 and current_sha != expected_old_sha256:
            return {
                "status": "conflict",
                "file_path": file_path,
                "current_sha256": current_sha,
                "expected_old_sha256": expected_old_sha256,
                "message": "File changed since preview. Refusing apply.",
            }

        backup_info = self._create_backup(file_path, current_content)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_content, encoding="utf-8")

        result = {
            "status": "ok",
            "file_path": file_path,
            "backup_id": backup_info["backup_id"],
            "old_sha256": current_sha,
            "new_sha256": self._sha256_text(new_content),
        }
        self._persist_trace("patch.apply", result)
        return result

    def rollback_patch(self, backup_id: str) -> dict:
        meta_path = self.backup_dir / backup_id / "meta.json"
        if not meta_path.exists():
            return {"status": "not_found", "backup_id": backup_id}

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        target = self.project_root / meta["file_path"]
        backup_file = self.backup_dir / backup_id / "content.bak"

        if not backup_file.exists():
            return {"status": "not_found", "backup_id": backup_id, "message": "Backup content missing"}

        restored = backup_file.read_text(encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(restored, encoding="utf-8")

        result = {
            "status": "ok",
            "backup_id": backup_id,
            "file_path": meta["file_path"],
            "restored_sha256": self._sha256_text(restored),
        }
        self._persist_trace("patch.rollback", result)
        return result

    def verify_patch(self, file_path: str) -> dict:
        target = self.project_root / file_path
        if not target.exists():
            return {"status": "not_found", "file_path": file_path}

        content = self._read_text(target)
        checks = {
            "exists": True,
            "non_empty": bool(content.strip()),
            "sha256": self._sha256_text(content),
            "size": len(content),
        }
        if file_path.endswith(".py"):
            try:
                compile(content, file_path, "exec")
                checks["python_syntax"] = "ok"
            except Exception as exc:
                checks["python_syntax"] = f"error: {exc}"

        result = {"status": "ok", "file_path": file_path, "checks": checks}
        self._persist_trace("patch.verify", result)
        return result

    def list_backups(self, limit: int = 50) -> dict:
        items = []
        dirs = sorted(
            [p for p in self.backup_dir.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for folder in dirs[:limit]:
            meta_path = folder / "meta.json"
            if not meta_path.exists():
                continue
            try:
                items.append(json.loads(meta_path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return {"status": "ok", "items": items}

    def _create_backup(self, file_path: str, content: str) -> dict:
        backup_id = str(uuid.uuid4())
        folder = self.backup_dir / backup_id
        folder.mkdir(parents=True, exist_ok=True)

        (folder / "content.bak").write_text(content, encoding="utf-8")
        meta = {
            "backup_id": backup_id,
            "file_path": file_path,
            "created_at": time.time(),
            "sha256": self._sha256_text(content),
        }
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return path.read_text(encoding="utf-8", errors="ignore")

    def _sha256_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

    def _persist_trace(self, event_name: str, payload: dict) -> None:
        if self.run_trace_service and hasattr(self.run_trace_service, "create_run"):
            try:
                run = self.run_trace_service.create_run(payload.get("file_path", event_name), source="safe_patch_engine")
                self.run_trace_service.add_event(run["id"], event_name, payload)
                self.run_trace_service.add_artifact(run["id"], event_name, "json", payload)
                self.run_trace_service.update_status(run["id"], "completed", summary=f"{event_name} completed")
            except Exception:
                pass
