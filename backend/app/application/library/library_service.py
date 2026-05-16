"""Library service: manages user-uploaded context files.

Extracted from services/library_service.py.  services/library_service.py is now a
thin re-export facade; all logic lives here.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
import sqlite3

from app.core.config import DATA_DIR, UPLOAD_DIR

SQLITE_DB = DATA_DIR / "library.db"
LEGACY_UPLOADS_DIR = UPLOAD_DIR

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


def add_library_file(filename: str, contents: bytes, file_type: str, use_in_context: bool = True) -> dict[str, Any]:
    """Store uploaded file on disk, extract preview, insert metadata into DB. Returns metadata dict."""
    from app.application.library.document_preview import extract_preview, safe_disk_name
    from app.infrastructure.db.library_db import insert_file

    disk_name = safe_disk_name(filename, contents)
    disk_path = LEGACY_UPLOADS_DIR / disk_name
    LEGACY_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    disk_path.write_bytes(contents)

    preview = extract_preview(filename, contents)
    sha256 = hashlib.sha256(contents).hexdigest()

    file_id = insert_file(filename, len(contents), file_type, preview, use_in_context, str(disk_path), sha256)
    return {
        "ok": True,
        "id": file_id,
        "name": filename,
        "preview_len": len(preview),
        "stored_path": str(disk_path),
    }


def remove_library_file_by_id(file_id: int) -> dict[str, Any]:
    """Delete file record from DB and remove the file from disk."""
    from app.infrastructure.db.library_db import delete_file

    stored_path = delete_file(file_id)
    if stored_path:
        try:
            p = Path(stored_path)
            if p.exists() and p.is_file():
                p.unlink()
        except Exception:
            pass
    return {"ok": True, "deleted_id": file_id}


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
