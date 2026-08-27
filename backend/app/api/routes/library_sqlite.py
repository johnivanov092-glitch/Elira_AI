"""
library_sqlite.py - SQLite-backed file library routes.

API:
  GET  /api/lib/list
  POST /api/lib/add
  POST /api/lib/import-resource
  POST /api/lib/toggle
  DELETE /api/lib/{id}
  POST /api/lib/search
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.config import MAX_UPLOAD_BYTES
from app.application.library.runtime import (
    add_file_contents,
    delete_file,
    get_context_files,
    import_resource,
    list_files,
    search_files,
    toggle_context,
)

router = APIRouter(prefix="/api/lib", tags=["library-v2"])


@router.get("/list")
def list_library_route():
    return list_files()


@router.post("/add")
async def add_file(
    file: UploadFile = File(...),
    use_in_context: bool = Form(True),
):
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB")
    # add_file_contents runs vision / OCR / audio transcription (network calls —
    # seconds, up to minutes for audio) plus a SQLite write. Offload to a worker
    # thread so a large upload can't block the event loop and stall other requests.
    return await asyncio.to_thread(
        add_file_contents,
        filename=file.filename or "unknown",
        contents=contents,
        content_type=file.content_type,
        use_in_context=use_in_context,
    )


@router.post("/import-resource")
async def import_resource_route(
    resource_id: str = Form(...),
    use_in_context: bool = Form(True),
):
    return await asyncio.to_thread(
        import_resource,
        resource_id,
        use_in_context=use_in_context,
    )


@router.post("/toggle")
async def toggle_context_route(file_id: int = Form(...), enabled: bool = Form(True)):
    return await asyncio.to_thread(toggle_context, file_id, enabled=enabled)


@router.delete("/{file_id}")
def delete_file_route(file_id: int):
    return delete_file(file_id)


@router.post("/search")
def search_files_route(query: str = Form("")):
    return search_files(query)


@router.get("/context")
def get_context_files_route():
    return get_context_files()
