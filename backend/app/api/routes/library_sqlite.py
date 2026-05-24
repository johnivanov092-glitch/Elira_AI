"""
library_sqlite.py - SQLite-backed file library routes.

API:
  GET  /api/lib/list
  POST /api/lib/add
  POST /api/lib/toggle
  DELETE /api/lib/{id}
  POST /api/lib/search
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from app.application.library.runtime import (
    add_file_contents,
    delete_file,
    get_context_files,
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
    return add_file_contents(
        filename=file.filename or "unknown",
        contents=await file.read(),
        content_type=file.content_type,
        use_in_context=use_in_context,
    )


@router.post("/toggle")
def toggle_context_route(file_id: int = Form(...), enabled: bool = Form(True)):
    return toggle_context(file_id, enabled=enabled)


@router.delete("/{file_id}")
def delete_file_route(file_id: int):
    return delete_file(file_id)


@router.post("/search")
def search_files_route(query: str = Form("")):
    return search_files(query)


@router.get("/context")
def get_context_files_route():
    return get_context_files()
