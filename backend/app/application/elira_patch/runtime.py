from __future__ import annotations

import difflib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.infrastructure.db.connection import connect_sqlite

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

VERIFY_CHECK_FILE_EXISTS = "\u0424\u0430\u0439\u043b \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u0435\u0442"
VERIFY_CHECK_FILE_READABLE = "\u0424\u0430\u0439\u043b \u0447\u0438\u0442\u0430\u0435\u0442\u0441\u044f \u043a\u0430\u043a UTF-8"
VERIFY_CHECK_LINES = "\u0421\u0442\u0440\u043e\u043a: {line_count}"
VERIFY_CHECK_SIZE = "\u0420\u0430\u0437\u043c\u0435\u0440: {file_size} \u0431\u0430\u0439\u0442"
VERIFY_CHECK_MATCHES_DISK = "\u0421\u043e\u0432\u043f\u0430\u0434\u0430\u0435\u0442 \u0441 \u0434\u0438\u0441\u043a\u043e\u043c"
VERIFY_CHECK_DIFFERS_FROM_DISK = (
    "\u041e\u0442\u043b\u0438\u0447\u0430\u0435\u0442\u0441\u044f \u043e\u0442 "
    "\u0432\u0435\u0440\u0441\u0438\u0438 \u043d\u0430 \u0434\u0438\u0441\u043a\u0435"
)


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


def resolve_project_path(rel_path: str) -> tuple[Path | None, str | None]:
    target = (PROJECT_ROOT / rel_path).resolve()

    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        return None, "outside_root"

    parts = set(target.parts)
    if parts & BLOCKED_PARTS:
        return None, "blocked"

    return target, None


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


def diff_patch(path: str, original: str, updated: str):
    diff_text = build_diff_text(path, original, updated)
    return {
        "status": "ok",
        "path": path,
        "diff_text": diff_text,
        "stats": diff_stats(diff_text),
    }


def apply_patch(path: str, content: str):
    target, error = resolve_project_path(path)
    if error:
        return None, error

    if target.is_dir():
        return None, "is_directory"
    if not target.exists():
        return None, "target_not_found"

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    backup = backup_file_path(path)
    ensure_parent(backup)

    before_content = target.read_text(encoding="utf-8")
    shutil.copy2(target, backup)
    target.write_text(content, encoding="utf-8")
    history_id = write_history(path, "apply", before_content, content)

    return {
        "status": "ok",
        "path": path,
        "backup_path": str(backup.relative_to(PROJECT_ROOT)),
        "history_id": history_id,
        "applied_at": datetime.utcnow().isoformat(),
    }, None


def apply_batch(items: list[dict[str, Any]]):
    if not items:
        return None, "no_items", None

    results = []
    resolved_items = []
    for item in items:
        item_path = str(item["path"])
        target, error = resolve_project_path(item_path)
        if error:
            return None, error, item_path
        if target.is_dir():
            return None, "is_directory", item_path
        if not target.exists():
            return None, "target_not_found", item_path
        resolved_items.append((item, target))

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    for item, target in resolved_items:
        item_path = str(item["path"])
        item_content = item["content"]
        backup = backup_file_path(item_path)
        ensure_parent(backup)
        before_content = target.read_text(encoding="utf-8")
        shutil.copy2(target, backup)
        target.write_text(item_content, encoding="utf-8")
        history_id = write_history(item_path, "apply-batch", before_content, item_content)
        results.append(
            {
                "path": item_path,
                "history_id": history_id,
                "backup_path": str(backup.relative_to(PROJECT_ROOT)),
            }
        )

    return {
        "status": "ok",
        "count": len(results),
        "items": results,
        "applied_at": datetime.utcnow().isoformat(),
    }, None, None


def rollback_patch(path: str):
    target, error = resolve_project_path(path)
    if error:
        return None, error

    backup = backup_file_path(path)

    if not backup.exists():
        return None, "no_backup"
    if target.is_dir():
        return None, "is_directory"

    before_content = target.read_text(encoding="utf-8")
    backup_content = backup.read_text(encoding="utf-8")
    shutil.copy2(backup, target)
    history_id = write_history(path, "rollback", before_content, backup_content)

    return {
        "status": "ok",
        "path": path,
        "history_id": history_id,
        "rolled_back_at": datetime.utcnow().isoformat(),
    }, None


def verify_patch(path: str, content: str | None = None):
    target, error = resolve_project_path(path)
    if error:
        return None, error

    if target.is_dir():
        return None, "is_directory"
    if not target.exists():
        return None, "target_not_found"

    disk_content = target.read_text(encoding="utf-8")
    compare_content = content if content is not None else disk_content

    changed = compare_content != disk_content
    line_count = max(1, compare_content.count("\n") + 1)
    file_size = len(compare_content.encode("utf-8"))
    diff_text = build_diff_text(path, disk_content, compare_content)

    checks = [
        VERIFY_CHECK_FILE_EXISTS,
        VERIFY_CHECK_FILE_READABLE,
        VERIFY_CHECK_LINES.format(line_count=line_count),
        VERIFY_CHECK_SIZE.format(file_size=file_size),
        VERIFY_CHECK_MATCHES_DISK if not changed else VERIFY_CHECK_DIFFERS_FROM_DISK,
    ]

    return {
        "status": "ok",
        "path": path,
        "changed_vs_disk": changed,
        "checks": checks,
        "stats": diff_stats(diff_text),
        "diff_text": diff_text,
        "verified_at": datetime.utcnow().isoformat(),
    }, None


def verify_batch(items: list[dict[str, Any]]):
    if not items:
        return None, "no_items", None

    results = []
    total_added = 0
    total_removed = 0
    for item in items:
        item_path = str(item["path"])
        target, error = resolve_project_path(item_path)
        if error:
            return None, error, item_path
        if target.is_dir():
            return None, "is_directory", item_path
        if not target.exists():
            return None, "target_not_found", item_path

        disk_content = target.read_text(encoding="utf-8")
        compare_content = item.get("content") if item.get("content") is not None else disk_content
        diff_text = build_diff_text(item_path, disk_content, compare_content)
        stats = diff_stats(diff_text)
        total_added += stats["added"]
        total_removed += stats["removed"]

        results.append(
            {
                "path": item_path,
                "changed_vs_disk": compare_content != disk_content,
                "stats": stats,
                "diff_text": diff_text,
            }
        )

    return {
        "status": "ok",
        "count": len(results),
        "items": results,
        "summary": {"added": total_added, "removed": total_removed},
        "verified_at": datetime.utcnow().isoformat(),
    }, None, None


def list_history(path: str = "", limit: int = 50):
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
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


def get_history_item(item_id: int):
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
    try:
        row = conn.execute(
            """
            SELECT id, path, action, before_content, after_content, diff_text, created_at
            FROM patch_history
            WHERE id = ?
            """,
            (item_id,),
        ).fetchone()

        if not row:
            return None, "history_not_found"

        data = dict(row)
        data["stats"] = diff_stats(data.get("diff_text") or "")
        return data, None
    finally:
        conn.close()
