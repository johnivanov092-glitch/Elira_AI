"""HTTP layer for the file text-extraction endpoint.

All extraction logic lives in
``app.application.file_extract.runtime``; this module keeps only the
FastAPI router and the thin async delegating handler.
"""
from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from app.application.file_extract import runtime as extract_runtime

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/extract-text")
async def extract_text(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        filename = (file.filename or "").strip()
        return extract_runtime.extract_file(filename, contents)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
