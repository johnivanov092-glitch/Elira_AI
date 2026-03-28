from __future__ import annotations

from pathlib import Path
from typing import Any
import sqlite3

BASE_DIR = Path(__file__).resolve().parents[3]
PROJECT_ROOT = BASE_DIR
SQLITE_DB = PROJECT_ROOT / "backend" / "data" / "library.db"
LEGACY_UPLOADS_DIR = PROJECT_ROOT / "data" / "uploads"

TEXT_EXTS = {".txt", ".md", ".py", ".json", ".csv", ".yml", ".yaml", ".log", ".html", ".js", ".ts", ".css"}


def _conn() -> sqlite3.Connection:
    SQLITE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _read_disk_preview(stored_path: str, max_chars: int) -> str:
    if not stored_path:
        return ""
    try:
        return Path(stored_path).read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""


def list_library_files() -> dict[str, Any]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, name, size, use_in_context, stored_path, type, source, created_at FROM files ORDER BY created_at DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()
    files = []
    for row in rows:
        files.append(
            {
                "id": row["id"],
                "name": row["name"],
                "size": row["size"] or 0,
                "active": bool(row["use_in_context"]),
                "path": row["stored_path"] or str(LEGACY_UPLOADS_DIR / row["name"]),
                "suffix": Path(row["name"]).suffix.lower(),
                "type": row["type"] or "unknown",
                "source": row["source"] or "upload",
                "created_at": row["created_at"],
            }
        )
    return {"ok": True, "files": files, "count": len(files)}


def set_library_active(filename: str, active: bool) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT id, name FROM files WHERE name = ? ORDER BY id DESC LIMIT 1", (filename,)).fetchone()
        if row:
            conn.execute("UPDATE files SET use_in_context = ? WHERE id = ?", (1 if active else 0, row["id"]))
            conn.commit()
            return {"ok": True, "filename": filename, "active": bool(active)}
        return {"ok": False, "error": f"Файл не найден: {filename}"}
    finally:
        conn.close()


def delete_library_file(filename: str) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT id, stored_path FROM files WHERE name = ? ORDER BY id DESC LIMIT 1", (filename,)).fetchone()
        if not row:
            return {"ok": False, "error": f"Файл не найден: {filename}"}
        conn.execute("DELETE FROM files WHERE id = ?", (row["id"],))
        conn.commit()
    finally:
        conn.close()

    stored_path = row["stored_path"] or ""
    if stored_path:
        try:
            path = Path(stored_path)
            if path.exists() and path.is_file():
                path.unlink()
        except Exception:
            pass
    return {"ok": True, "filename": filename}


def build_library_context(max_files: int = 3, max_chars_per_file: int = 4000) -> dict[str, Any]:
    conn = _conn()
    rows = conn.execute(
        "SELECT name, preview, stored_path FROM files WHERE use_in_context = 1 ORDER BY created_at DESC, id DESC LIMIT ?",
        (max_files,),
    ).fetchall()
    conn.close()

    context_parts = []
    used_files = []
    active_count = 0
    for row in rows:
        active_count += 1
        suffix = Path(row["name"]).suffix.lower()
        content = (row["preview"] or "")[:max_chars_per_file]
        if not content and suffix in TEXT_EXTS:
            content = _read_disk_preview(row["stored_path"] or "", max_chars_per_file)
        if not content.strip():
            continue
        used_files.append(row["name"])
        context_parts.append(f"===== FILE: {row['name']} =====\n{content}")

    return {
        "ok": True,
        "used_files": used_files,
        "active_count": active_count,
        "context": "\n\n".join(context_parts),
    }
