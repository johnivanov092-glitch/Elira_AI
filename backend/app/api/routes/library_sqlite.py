"""HTTP layer for the Elira library file endpoints.

All business/storage logic lives in
``app.application.library_sqlite.runtime``; this module keeps only the
FastAPI router and the thin delegating handlers.
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from app.application.library_sqlite import runtime as lib_runtime

router = APIRouter(prefix="/api/lib", tags=["library-v2"])


@router.get("/list")
def list_files():
    return lib_runtime.list_files()


@router.post("/add")
async def add_file(
    file: UploadFile = File(...),
    use_in_context: bool = Form(True),
):
    contents = await file.read()
    return lib_runtime.add_file(
        file.filename or "unknown",
        contents,
        file.content_type,
        use_in_context,
    )


@router.post("/toggle")
def toggle_context(file_id: int = Form(...), enabled: bool = Form(True)):
    return lib_runtime.toggle_context(file_id, enabled)


@router.delete("/{file_id}")
def delete_file(file_id: int):
    return lib_runtime.delete_file(file_id)


@router.post("/search")
def search_files(query: str = Form("")):
    return lib_runtime.search_files(query)


@router.get("/context")
def get_context_files():
    return lib_runtime.get_context_files()
