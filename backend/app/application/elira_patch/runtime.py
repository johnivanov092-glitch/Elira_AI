"""Application-layer runtime for the Elira patch endpoints.

Owns path resolution (HTTP-free), diff/stats helpers, patch history DB,
and all FS operations (apply, rollback, verify, batch apply/verify).
Each FS operation returns ``(result | None, error_info | None)`` instead
of raising HTTPException.  The HTTP layer in
``api/routes/elira_patch.py`` is a thin FastAPI shell.
"""
from __future__ import annotations

import difflib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.infrastructure.db.connection import connect_sqlite


PROJECT_ROOT = Path(".").resolve()
DATA_ROOT = PROJECT_ROOT / "data"
BACKUP_ROOT = DATA_ROOT / "patch_backups"
DB_PATH = DATA_ROOT / "elira_state.db"

BLOCKED_PARTS = {
    ".git", "node_modules", ".venv", "__pycache__", "dist", "build", "target"
}


# ───────── DB ─────────

def ensure_db() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
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


# ───────── Path helpers (HTTP-free) ─────────

def resolve_project_path(rel_path: str) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve *rel_path* under PROJECT_ROOT.

    Returns ``(path, None)`` on success or ``(None, error_kind)`` where
    *error_kind* is ``"outside_root"`` or ``"blocked"``.
    """
    target = (PROJECT_ROOT / rel_path).resolve()
    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        return None, "outside_root"
    if set(target.parts) & BLOCKED_PARTS:
        return None, "blocked"
    return target, None


def backup_file_path(rel_path: str) -> Path:
    safe_name = rel_path.replace("\\", "__").replace("/", "__")
    return BACKUP_ROOT / f"{safe_name}.bak"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


# ───────── Diff helpers ─────────

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
        if line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return {"added": added, "removed": removed}


# ───────── History ─────────

def write_history(path: str, action: str, before_content: str, after_content: str) -> int:
    ensure_db()
    diff_text = build_diff_text(path, before_content, after_content)
    now = datetime.utcnow().isoformat()
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
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


def list_history(path: str = "", limit: int = 50) -> dict:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
    try:
        if path.strip():
            rows = conn.execute(
                """
                SELECT id, path, action, created_at
                FROM patch_history WHERE path = ?
                ORDER BY id DESC LIMIT ?
                """,
                (path, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, path, action, created_at
                FROM patch_history ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {"items": [dict(row) for row in rows]}
    finally:
        conn.close()


def get_history_item(item_id: int) -> dict:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
    try:
        row = conn.execute(
            """
            SELECT id, path, action, before_content, after_content, diff_text, created_at
            FROM patch_history WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        data = dict(row)
        data["stats"] = diff_stats(data.get("diff_text") or "")
        return data
    finally:
        conn.close()


# ───────── Patch operations (return (result, error_kind) tuples) ─────────
#
# error_kind values:
#   "outside_root"  → 403
#   "blocked"       → 403
#   "is_directory"  → 400
#   "not_found"     → 404
#   "no_backup"     → 404

def compute_diff(rel_path: str, original: str, updated: str) -> dict:
    """Pure diff computation; no path validation needed."""
    diff_text = build_diff_text(rel_path, original, updated)
    return {
        "status": "ok",
        "path": rel_path,
        "diff_text": diff_text,
        "stats": diff_stats(diff_text),
    }


def apply_patch(rel_path: str, content: str) -> Tuple[Optional[dict], Optional[str]]:
    target, err = resolve_project_path(rel_path)
    if err:
        return None, err
    assert target is not None
    if target.is_dir():
        return None, "is_directory"
    if not target.exists():
        return None, "not_found"

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    backup = backup_file_path(rel_path)
    ensure_parent(backup)

    before_content = target.read_text(encoding="utf-8")
    shutil.copy2(target, backup)
    target.write_text(content, encoding="utf-8")
    history_id = write_history(rel_path, "apply", before_content, content)

    return {
        "status": "ok",
        "path": rel_path,
        "backup_path": str(backup.relative_to(PROJECT_ROOT)),
        "history_id": history_id,
        "applied_at": datetime.utcnow().isoformat(),
    }, None


def rollback_patch(rel_path: str) -> Tuple[Optional[dict], Optional[str]]:
    target, err = resolve_project_path(rel_path)
    if err:
        return None, err
    assert target is not None
    backup = backup_file_path(rel_path)

    if not backup.exists():
        return None, "no_backup"
    if target.is_dir():
        return None, "is_directory"

    before_content = target.read_text(encoding="utf-8")
    backup_content = backup.read_text(encoding="utf-8")
    shutil.copy2(backup, target)
    history_id = write_history(rel_path, "rollback", before_content, backup_content)

    return {
        "status": "ok",
        "path": rel_path,
        "history_id": history_id,
        "rolled_back_at": datetime.utcnow().isoformat(),
    }, None


def verify_patch(rel_path: str, content: Optional[str]) -> Tuple[Optional[dict], Optional[str]]:
    target, err = resolve_project_path(rel_path)
    if err:
        return None, err
    assert target is not None
    if target.is_dir():
        return None, "is_directory"
    if not target.exists():
        return None, "not_found"

    disk_content = target.read_text(encoding="utf-8")
    compare_content = content if content is not None else disk_content
    changed = compare_content != disk_content
    line_count = max(1, compare_content.count("\n") + 1)
    file_size = len(compare_content.encode("utf-8"))
    diff_text = build_diff_text(rel_path, disk_content, compare_content)

    checks = [
        "File exists",
        "File readable as UTF-8",
        f"Lines: {line_count}",
        f"Size: {file_size} bytes",
        "Matches disk" if not changed else "Differs from disk version",
    ]

    return {
        "status": "ok",
        "path": rel_path,
        "changed_vs_disk": changed,
        "checks": checks,
        "stats": diff_stats(diff_text),
        "diff_text": diff_text,
        "verified_at": datetime.utcnow().isoformat(),
    }, None


# ── Batch helpers ─────────────────────────────────────────────────────────────

BatchItem = Dict[str, Any]


def _validate_paths(items: List[BatchItem]) -> Optional[Tuple[str, str]]:
    """Validate all items' paths.  Returns ``(error_kind, path)`` on first failure."""
    for item in items:
        target, err = resolve_project_path(item["path"])
        if err:
            return err, item["path"]
        assert target is not None
        if target.is_dir():
            return "is_directory", item["path"]
        if not target.exists():
            return "not_found", item["path"]
    return None


def batch_apply(items: List[BatchItem]) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
    """Apply many patches atomically (validate all first, then apply all).

    Returns ``(result, None, None)`` or ``(None, error_kind, error_path)``.
    """
    validation_err = _validate_paths(items)
    if validation_err:
        kind, path = validation_err
        return None, kind, path

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for item in items:
        target, _ = resolve_project_path(item["path"])
        assert target is not None
        backup = backup_file_path(item["path"])
        ensure_parent(backup)
        before_content = target.read_text(encoding="utf-8")
        shutil.copy2(target, backup)
        target.write_text(item["content"], encoding="utf-8")
        history_id = write_history(item["path"], "apply-batch", before_content, item["content"])
        results.append({
            "path": item["path"],
            "history_id": history_id,
            "backup_path": str(backup.relative_to(PROJECT_ROOT)),
        })

    return {
        "status": "ok",
        "count": len(results),
        "items": results,
        "applied_at": datetime.utcnow().isoformat(),
    }, None, None


def batch_verify(items: List[BatchItem]) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
    """Verify many paths against optional content.

    Returns ``(result, None, None)`` or ``(None, error_kind, error_path)``.
    """
    validation_err = _validate_paths(items)
    if validation_err:
        kind, path = validation_err
        return None, kind, path

    results = []
    total_added = total_removed = 0
    for item in items:
        target, _ = resolve_project_path(item["path"])
        assert target is not None
        disk_content = target.read_text(encoding="utf-8")
        compare_content = item.get("content") if item.get("content") is not None else disk_content
        diff_text = build_diff_text(item["path"], disk_content, compare_content)
        stats = diff_stats(diff_text)
        total_added += stats["added"]
        total_removed += stats["removed"]
        results.append({
            "path": item["path"],
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
    }, None, None
