from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path("data/library.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def list_library_files() -> dict[str, Any]:
    c = _conn()
    rows = c.execute("SELECT * FROM files ORDER BY created_at DESC").fetchall()
    c.close()
    files = []
    for row in rows:
        item = dict(row)
        files.append({
            "id": item.get("id"),
            "name": item.get("name", ""),
            "size": item.get("size", 0),
            "active": bool(item.get("use_in_context", 0)),
            "path": f"sqlite://library/{item.get('id')}",
            "suffix": Path(str(item.get("name", ""))).suffix.lower(),
            "preview": item.get("preview", ""),
            "type": item.get("type", "unknown"),
            "source": item.get("source", "upload"),
            "created_at": item.get("created_at"),
        })
    return {"ok": True, "files": files, "count": len(files)}


def set_library_active(filename: str, active: bool) -> dict[str, Any]:
    c = _conn()
    c.execute("UPDATE files SET use_in_context = ? WHERE name = ?", (1 if active else 0, filename))
    c.commit()
    changed = c.total_changes
    c.close()
    return {"ok": True, "filename": filename, "active": bool(active), "updated": changed}


def delete_library_file(filename: str) -> dict[str, Any]:
    c = _conn()
    c.execute("DELETE FROM files WHERE name = ?", (filename,))
    c.commit()
    changed = c.total_changes
    c.close()
    return {"ok": True, "filename": filename, "deleted": changed}


def build_library_context(max_files: int = 3, max_chars_per_file: int = 4000) -> dict[str, Any]:
    c = _conn()
    rows = c.execute(
        "SELECT id, name, preview FROM files WHERE use_in_context = 1 AND preview != '' ORDER BY created_at DESC LIMIT ?",
        (max(1, int(max_files)),),
    ).fetchall()
    c.close()

    context_parts = []
    used_files = []
    for row in rows:
        item = dict(row)
        preview = str(item.get("preview", "") or "")[: max(1, int(max_chars_per_file))]
        if not preview.strip():
            continue
        used_files.append(item.get("name", ""))
        context_parts.append(f"===== FILE: {item.get('name', '')} =====\n{preview}")

    return {
        "ok": True,
        "used_files": used_files,
        "active_count": len(used_files),
        "context": "\n\n".join(context_parts),
    }
