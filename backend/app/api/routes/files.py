"""
files.py - извлечение текста из файлов.

Поддержка: PDF, DOCX, XLSX, ZIP, BAS, VBA, CLS, FRM, RSC, и все текстовые.
"""
import asyncio

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.application.file_extract import runtime as file_extract_runtime
from app.core.config import MAX_UPLOAD_BYTES

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/extract-text")
async def extract_text(file: UploadFile = File(...)):
    """Извлекает текст из любого поддерживаемого файла."""
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB")
    try:
        # extract_file does blocking parsing (PDF/DOCX/XLSX) — off-load it.
        return await asyncio.to_thread(file_extract_runtime.extract_file, file.filename or "", contents)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
