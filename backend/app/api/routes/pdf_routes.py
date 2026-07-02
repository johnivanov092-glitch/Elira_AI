"""
pdf_routes.py — API для продвинутой работы с PDF.

Эндпоинты:
  POST /api/pdf/extract     — умное извлечение (текст + таблицы + OCR)
  POST /api/pdf/tables      — таблицы → Excel
  POST /api/pdf/to-word     — PDF → DOCX конвертация
  POST /api/pdf/analyze     — подробный анализ PDF
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

from app.application.pdf import runtime as pdf_runtime
from app.core.config import MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pdf", tags=["pdf-pro"])


async def _read_pdf_upload(file: UploadFile) -> bytes:
    """Read an uploaded file and reject oversized bodies (413) BEFORE the handler's
    try/except, so the size guard isn't swallowed into a generic 500."""
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB")
    return data


@router.post("/extract")
async def api_extract(file: UploadFile = File(...)):
    """Умное извлечение: pypdf → pdfplumber → OCR."""
    data = await _read_pdf_upload(file)
    try:
        result = await asyncio.to_thread(pdf_runtime.extract_pdf_smart, data)
        return {
            "ok": True,
            "filename": file.filename,
            "text": result["text"],
            "tables": result["tables"],
            "pages": result["pages"],
            "method": result["method"],
            "ocr_used": result["ocr_used"],
        }
    except Exception:
        logger.exception("pdf route failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "PDF processing failed"})


@router.post("/tables")
async def api_tables(file: UploadFile = File(...)):
    """Извлечь таблицы из PDF → Excel."""
    data = await _read_pdf_upload(file)
    try:
        return await asyncio.to_thread(pdf_runtime.pdf_tables_to_excel, data, filename=f"{file.filename}_tables")
    except Exception:
        logger.exception("pdf route failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "PDF processing failed"})


@router.post("/to-word")
async def api_to_word(file: UploadFile = File(...)):
    """Конвертировать PDF → Word."""
    data = await _read_pdf_upload(file)
    try:
        return await asyncio.to_thread(pdf_runtime.pdf_to_word, data, filename=file.filename.replace(".pdf", ""))
    except Exception:
        logger.exception("pdf route failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "PDF processing failed"})


@router.post("/analyze")
async def api_analyze(file: UploadFile = File(...)):
    """Подробный анализ PDF."""
    data = await _read_pdf_upload(file)
    try:
        return await asyncio.to_thread(pdf_runtime.analyze_pdf, data)
    except Exception:
        logger.exception("pdf route failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "PDF processing failed"})


@router.post("/preview")
async def api_preview(file: UploadFile = File(...), pages: str = "1,2,3"):
    """Рендерит страницы PDF как PNG картинки."""
    data = await _read_pdf_upload(file)
    try:
        page_list = [int(p.strip()) for p in pages.split(",") if p.strip().isdigit()]
        return await asyncio.to_thread(pdf_runtime.render_pdf_pages, data, page_list or None)
    except Exception:
        logger.exception("pdf route failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "PDF processing failed"})
