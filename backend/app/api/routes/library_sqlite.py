"""
library_sqlite.py — library file management (metadata in SQLite, files on disk).

API:
  GET    /api/lib/list     — list all files
  POST   /api/lib/add      — upload file
  POST   /api/lib/toggle   — toggle context inclusion
  DELETE /api/lib/{id}     — delete file
  POST   /api/lib/search   — search files
  GET    /api/lib/context  — context-enabled files for LLM
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile

from app.core.config import UPLOAD_DIR
from app.infrastructure.db.library_db import (
    delete_file,
    get_context_files,
    insert_file,
    list_files,
    search_files,
    toggle_file,
)
from app.application.library.document_preview import extract_preview, safe_disk_name

UPLOADS_DIR = UPLOAD_DIR
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(prefix="/api/lib", tags=["library-v2"])


@router.get("/list")
def route_list_files():
    return {"ok": True, "items": list_files(), "count": len(list_files())}


@router.post("/add")
async def route_add_file(file: UploadFile = File(...), use_in_context: bool = Form(True)):
    contents = await file.read()
    filename = file.filename or "unknown"
    if not contents:
        return {"ok": False, "error": "empty file"}

    disk_name = safe_disk_name(filename, contents)
    disk_path = UPLOADS_DIR / disk_name
    disk_path.write_bytes(contents)

    preview = extract_preview(filename, contents)
    sha256 = hashlib.sha256(contents).hexdigest()
    file_type = file.content_type or Path(filename).suffix.lower() or "unknown"

    file_id = insert_file(filename, len(contents), file_type, preview, use_in_context, str(disk_path), sha256)
    return {
        "ok": True,
        "id": file_id,
        "name": filename,
        "preview_len": len(preview),
        "stored_path": str(disk_path),
    }


@router.post("/toggle")
def route_toggle(file_id: int = Form(...), enabled: bool = Form(True)):
    toggle_file(file_id, enabled)
    return {"ok": True, "id": file_id, "use_in_context": enabled}


@router.delete("/{file_id}")
def route_delete(file_id: int):
    stored_path = delete_file(file_id)
    if stored_path:
        try:
            p = Path(stored_path)
            if p.exists() and p.is_file():
                p.unlink()
        except Exception:
            pass
    return {"ok": True, "deleted_id": file_id}


@router.post("/search")
def route_search(query: str = Form("")):
    items = search_files(query)
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/context")
def route_context_files():
    items = get_context_files()
    return {"ok": True, "items": items, "count": len(items)}
