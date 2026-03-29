from __future__ import annotations

import difflib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/elira/patch", tags=["elira-patch"])

PROJECT_ROOT = Path(".").resolve()
DATA_ROOT = PROJECT_ROOT / "data"
BACKUP_ROOT = DATA_ROOT / "patch_backups"
DB_PATH = DATA_ROOT / "elira_state.db"

BLOCKED_PARTS = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "target",
}


class ApplyPatchPayload(BaseModel):
    path: str = Field(min_length=1)
    content: str


class RollbackPayload(BaseModel):
    path: str = Field(min_length=1)


class VerifyPayload(BaseModel):
    path: str = Field(min_length=1)
    content: Optional[str] = None


class DiffPayload(BaseModel):
    path: str = Field(min_length=1)
    original: str
    updated: str


class BatchApplyItem(BaseModel):
    path: str = Field(min_length=1)
    content: str


class BatchApplyPayload(BaseModel):
    items: List[BatchApplyItem]


class BatchVerifyItem(BaseModel):
    path: str = Field(min_length=1)
    content: Optional[str] = None


class BatchVerifyPayload(BaseModel):
    items: List[BatchVerifyItem]


def ensure_db() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS patch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                action TEXT NOT NULL,
                before_content TEXT,
                after_content TEXT,
                diff_text TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def resolve_project_path(rel_path: str) -> Path:
    target = (PROJECT_ROOT / rel_path).resolve()

    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path is outside project root")

    parts = set(target.parts)
    if parts & BLOCKED_PARTS:
        raise HTTPException(status_code=403, detail="Path points to blocked area")

    return target


def backup_file_path(rel_path: str) -> Path:
    safe_name = rel_path.replace("\\", "__").replace("/", "__")
    return BACKUP_ROOT / f"{safe_name}.bak"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


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
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return {"added": added, "removed": removed}


def write_history(path: str, action: str, before_content: str, after_content: str) -> int:
    ensure_db()
    diff_text = build_diff_text(path, before_content, after_content)
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """
            INSERT INTO patch_history (
                path, action, before_content, after_content, diff_text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (path, action, before_content, after_content, diff_text, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@router.post("/diff")
def diff_patch(payload: DiffPayload):
    diff_text = build_diff_text(payload.path, payload.original, payload.updated)
    return {
        "status": "ok",
        "path": payload.path,
        "diff_text": diff_text,
        "stats": diff_stats(diff_text),
    }


@router.post("/apply")
def apply_patch(payload: ApplyPatchPayload):
    target = resolve_project_path(payload.path)

    if target.is_dir():
        raise HTTPException(status_code=400, detail="Path points to directory")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Target file not found")

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    backup = backup_file_path(payload.path)
    ensure_parent(backup)

    before_content = target.read_text(encoding="utf-8")
    shutil.copy2(target, backup)
    target.write_text(payload.content, encoding="utf-8")
    history_id = write_history(payload.path, "apply", before_content, payload.content)

    return {
        "status": "ok",
        "path": payload.path,
        "backup_path": str(backup.relative_to(PROJECT_ROOT)),
        "history_id": history_id,
        "applied_at": datetime.utcnow().isoformat(),
    }


@router.post("/apply-batch")
def apply_batch(payload: BatchApplyPayload):
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items provided")

    results = []
    for item in payload.items:
        target = resolve_project_path(item.path)
        if target.is_dir():
            raise HTTPException(status_code=400, detail=f"Directory path: {item.path}")
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Target file not found: {item.path}")

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    for item in payload.items:
        target = resolve_project_path(item.path)
        backup = backup_file_path(item.path)
        ensure_parent(backup)
        before_content = target.read_text(encoding="utf-8")
        shutil.copy2(target, backup)
        target.write_text(item.content, encoding="utf-8")
        history_id = write_history(item.path, "apply-batch", before_content, item.content)
        results.append({
            "path": item.path,
            "history_id": history_id,
            "backup_path": str(backup.relative_to(PROJECT_ROOT)),
        })

    return {
        "status": "ok",
        "count": len(results),
        "items": results,
        "applied_at": datetime.utcnow().isoformat(),
    }


@router.post("/rollback")
def rollback_patch(payload: RollbackPayload):
    target = resolve_project_path(payload.path)
    backup = backup_file_path(payload.path)

    if not backup.exists():
        raise HTTPException(status_code=404, detail="Backup not found for rollback")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Path points to directory")

    before_content = target.read_text(encoding="utf-8")
    backup_content = backup.read_text(encoding="utf-8")
    shutil.copy2(backup, target)
    history_id = write_history(payload.path, "rollback", before_content, backup_content)

    return {
        "status": "ok",
        "path": payload.path,
        "history_id": history_id,
        "rolled_back_at": datetime.utcnow().isoformat(),
    }


@router.post("/verify")
def verify_patch(payload: VerifyPayload):
    target = resolve_project_path(payload.path)

    if target.is_dir():
        raise HTTPException(status_code=400, detail="Path points to directory")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Target file not found")

    disk_content = target.read_text(encoding="utf-8")
    compare_content = payload.content if payload.content is not None else disk_content

    changed = compare_content != disk_content
    line_count = max(1, compare_content.count("\n") + 1)
    file_size = len(compare_content.encode("utf-8"))
    diff_text = build_diff_text(payload.path, disk_content, compare_content)

    checks = [
        "Файл существует",
        "Файл читается как UTF-8",
        f"Строк: {line_count}",
        f"Размер: {file_size} байт",
        "Совпадает с диском" if not changed else "Отличается от версии на диске",
    ]

    return {
        "status": "ok",
        "path": payload.path,
        "changed_vs_disk": changed,
        "checks": checks,
        "stats": diff_stats(diff_text),
        "diff_text": diff_text,
        "verified_at": datetime.utcnow().isoformat(),
    }


@router.post("/verify-batch")
def verify_batch(payload: BatchVerifyPayload):
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items provided")

    results = []
    total_added = 0
    total_removed = 0
    for item in payload.items:
        target = resolve_project_path(item.path)
        if target.is_dir():
            raise HTTPException(status_code=400, detail=f"Directory path: {item.path}")
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Target file not found: {item.path}")

        disk_content = target.read_text(encoding="utf-8")
        compare_content = item.content if item.content is not None else disk_content
        diff_text = build_diff_text(item.path, disk_content, compare_content)
        stats = diff_stats(diff_text)
        total_added += stats["added"]
        total_removed += stats["removed"]

        results.append({
            "path": item.path,
            "changed_vs_disk": compare_content != disk_content,
            "stats": stats,
            "diff_text": diff_text,
        })

    return {
        "status": "ok",
        "count": len(results),
        "items": results,
        "summary": {"added": total_added, "removed": total_removed},
        "verified_at": datetime.utcnow().isoformat(),
    }


@router.get("/history/list")
def list_history(path: str = "", limit: int = 50):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if path.strip():
            rows = conn.execute(
                """
                SELECT id, path, action, created_at
                FROM patch_history
                WHERE path = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (path, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, path, action, created_at
                FROM patch_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return {"items": [dict(row) for row in rows]}
    finally:
        conn.close()


@router.get("/history/get")
def get_history_item(id: int):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, path, action, before_content, after_content, diff_text, created_at
            FROM patch_history
            WHERE id = ?
            """,
            (id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="History item not found")

        data = dict(row)
        data["stats"] = diff_stats(data.get("diff_text") or "")
        return data
    finally:
        conn.close()
