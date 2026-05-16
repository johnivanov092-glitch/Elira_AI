"""
library_sqlite.py — library file management routes (thin HTTP layer).

All logic lives in app.application.library.library_service and
app.infrastructure.db.library_db.
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from app.infrastructure.db.library_db import (
    get_context_files,
    list_files,
    search_files,
    toggle_file,
)
from app.application.library.library_service import (
    add_library_file,
    remove_library_file_by_id,
)

router = APIRouter(prefix="/api/lib", tags=["library-v2"])


@router.get("/list")
def route_list_files():
    items = list_files()
    return {"ok": True, "items": items, "count": len(items)}


@router.post("/add")
async def route_add_file(file: UploadFile = File(...), use_in_context: bool = Form(True)):
    contents = await file.read()
    if not contents:
        return {"ok": False, "error": "empty file"}
    file_type = file.content_type or ""
    return add_library_file(file.filename or "unknown", contents, file_type, use_in_context)


@router.post("/toggle")
def route_toggle(file_id: int = Form(...), enabled: bool = Form(True)):
    toggle_file(file_id, enabled)
    return {"ok": True, "id": file_id, "use_in_context": enabled}


@router.delete("/{file_id}")
def route_delete(file_id: int):
    return remove_library_file_by_id(file_id)


@router.post("/search")
def route_search(query: str = Form("")):
    items = search_files(query)
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/context")
def route_context_files():
    items = get_context_files()
    return {"ok": True, "items": items, "count": len(items)}
