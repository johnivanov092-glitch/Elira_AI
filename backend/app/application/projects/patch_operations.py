"""Simple file patch operations for the elira-patch API.

Handles path validation, backup, diff computation, apply/rollback/verify.
History persistence is delegated to infrastructure/db/patch_history_db.py.
"""
from __future__ import annotations

import difflib
import shutil
from pathlib import Path

from fastapi import HTTPException

from app.infrastructure.db.patch_history_db import write_history

PROJECT_ROOT = Path(".").resolve()
BACKUP_ROOT = PROJECT_ROOT / "data" / "patch_backups"

BLOCKED_PARTS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build", "target"}


def resolve_project_path(rel_path: str) -> Path:
    target = (PROJECT_ROOT / rel_path).resolve()
    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path is outside project root")
    if set(target.parts) & BLOCKED_PARTS:
        raise HTTPException(status_code=403, detail="Path points to blocked area")
    return target


def backup_file_path(rel_path: str) -> Path:
    safe_name = rel_path.replace("\\", "__").replace("/", "__")
    return BACKUP_ROOT / f"{safe_name}.bak"


def build_diff_text(path: str, original: str, updated: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"{path} (current)",
            tofile=f"{path} (proposed)",
            lineterm="",
        )
    )


def diff_stats(diff_text: str) -> dict:
    added = removed = 0
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return {"added": added, "removed": removed}


def apply_file(rel_path: str, content: str, action: str = "apply") -> dict:
    target = resolve_project_path(rel_path)
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Path points to directory")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Target file not found")

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    backup = backup_file_path(rel_path)
    backup.parent.mkdir(parents=True, exist_ok=True)

    before_content = target.read_text(encoding="utf-8")
    shutil.copy2(target, backup)
    target.write_text(content, encoding="utf-8")

    diff_text = build_diff_text(rel_path, before_content, content)
    history_id = write_history(rel_path, action, before_content, content, diff_text)

    return {
        "backup_path": str(backup.relative_to(PROJECT_ROOT)),
        "history_id": history_id,
        "stats": diff_stats(diff_text),
    }


def rollback_file(rel_path: str) -> dict:
    target = resolve_project_path(rel_path)
    backup = backup_file_path(rel_path)

    if not backup.exists():
        raise HTTPException(status_code=404, detail="Backup not found for rollback")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Path points to directory")

    before_content = target.read_text(encoding="utf-8")
    backup_content = backup.read_text(encoding="utf-8")
    shutil.copy2(backup, target)

    diff_text = build_diff_text(rel_path, before_content, backup_content)
    history_id = write_history(rel_path, "rollback", before_content, backup_content, diff_text)

    return {"history_id": history_id}


def verify_file(rel_path: str, content: str | None) -> dict:
    target = resolve_project_path(rel_path)
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Path points to directory")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Target file not found")

    disk_content = target.read_text(encoding="utf-8")
    compare_content = content if content is not None else disk_content
    changed = compare_content != disk_content
    diff_text = build_diff_text(rel_path, disk_content, compare_content)
    line_count = max(1, compare_content.count("\n") + 1)
    file_size = len(compare_content.encode("utf-8"))

    return {
        "changed_vs_disk": changed,
        "diff_text": diff_text,
        "stats": diff_stats(diff_text),
        "line_count": line_count,
        "file_size": file_size,
    }
