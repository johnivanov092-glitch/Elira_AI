"""Persistent registry of known projects (name + path).

Separate from the single "active project" in advanced/runtime.py: this is the
saved LIST the user manages (add path -> appears in list, remove -> gone). The
UI's project switcher and "open project X by name" resolve against it. Opening
a project still goes through runtime.open_project (sets the active one).

Stored at data/projects.db.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

DB_PATH = sqlite_data_file("projects.db", key_tables=("projects",))


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def _init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


_init_db()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "path": row["path"], "created_at": row["created_at"]}


def list_projects() -> dict[str, Any]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return {"ok": True, "projects": [_row(r) for r in rows], "count": len(rows)}
    finally:
        conn.close()


def add_project(path: str, name: str = "") -> dict[str, Any]:
    """Add a project by path. Name defaults to the folder name. Path must be an
    existing directory. Re-adding the same (resolved) path updates its name."""
    raw = (path or "").strip()
    if not raw:
        return {"ok": False, "error": "Пустой путь"}
    try:
        resolved = Path(raw).expanduser().resolve()
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"Некорректный путь: {exc}"}
    if not resolved.is_dir():
        return {"ok": False, "error": f"Папка не найдена: {resolved}"}

    norm_path = str(resolved)
    label = (name or "").strip() or resolved.name or norm_path
    now = int(time.time() * 1000)

    conn = _conn()
    try:
        existing = conn.execute("SELECT id FROM projects WHERE path = ?", (norm_path,)).fetchone()
        if existing:
            conn.execute("UPDATE projects SET name = ? WHERE path = ?", (label, norm_path))
            conn.commit()
            pid = existing["id"]
        else:
            pid = f"p-{now}-{uuid.uuid4().hex[:6]}"
            conn.execute(
                "INSERT INTO projects (id, name, path, created_at) VALUES (?, ?, ?, ?)",
                (pid, label, norm_path, now),
            )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": pid, "name": label, "path": norm_path}


def remove_project(project_id: str) -> dict[str, Any]:
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
        return {"ok": True, "removed": cur.rowcount > 0}
    finally:
        conn.close()


def resolve_project(name_or_path: str) -> dict[str, Any] | None:
    """Resolve a saved project by exact name (case-insensitive) or by path.
    Returns the project row or None. Used so the chat can act on 'project X'."""
    needle = (name_or_path or "").strip()
    if not needle:
        return None
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM projects WHERE LOWER(name) = LOWER(?) OR path = ? LIMIT 1",
            (needle, needle),
        ).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()
