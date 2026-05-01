"""
files.py - извлечение текста из файлов.

Поддержка: PDF, DOCX, XLSX, ZIP, BAS, VBA, CLS, FRM, RSC, и все текстовые.
"""
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from app.application.file_extract import runtime as file_extract_runtime

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/extract-text")
async def extract_text(file: UploadFile = File(...)):
    """Извлекает текст из любого поддерживаемого файла."""
    try:
        contents = await file.read()
        return file_extract_runtime.extract_file(file.filename or "", contents)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
