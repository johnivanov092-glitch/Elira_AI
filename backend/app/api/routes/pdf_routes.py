"""
pdf_routes.py — API для продвинутой работы с PDF.

Эндпоинты:
  POST /api/pdf/extract     — умное извлечение (текст + таблицы + OCR)
  POST /api/pdf/tables      — таблицы → Excel
  POST /api/pdf/to-word     — PDF → DOCX конвертация
  POST /api/pdf/analyze     — подробный анализ PDF
"""
from __future__ import annotations

from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse

from app.services.pdf_pro import (
    extract_pdf_smart,
    pdf_tables_to_excel,
    pdf_to_word,
    analyze_pdf,
)

router = APIRouter(prefix="/api/pdf", tags=["pdf-pro"])


@router.post("/extract")
async def api_extract(file: UploadFile = File(...)):
    """Умное извлечение: pypdf → pdfplumber → OCR."""
    try:
        data = await file.read()
        result = extract_pdf_smart(data)
        return {
            "ok": True,
            "filename": file.filename,
            "text": result["text"],
            "tables": result["tables"],
            "pages": result["pages"],
            "method": result["method"],
            "ocr_used": result["ocr_used"],
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/tables")
async def api_tables(file: UploadFile = File(...)):
    """Извлечь таблицы из PDF → Excel."""
    try:
        data = await file.read()
        return pdf_tables_to_excel(data, filename=f"{file.filename}_tables")
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/to-word")
async def api_to_word(file: UploadFile = File(...)):
    """Конвертировать PDF → Word."""
    try:
        data = await file.read()
        return pdf_to_word(data, filename=file.filename.replace(".pdf", ""))
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/analyze")
async def api_analyze(file: UploadFile = File(...)):
    """Подробный анализ PDF."""
    try:
        data = await file.read()
        return analyze_pdf(data)
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/preview")
async def api_preview(file: UploadFile = File(...), pages: str = "1,2,3"):
    """Рендерит страницы PDF как PNG картинки."""
    try:
        from app.services.pdf_pro import render_pdf_pages
        data = await file.read()
        page_list = [int(p.strip()) for p in pages.split(",") if p.strip().isdigit()]
        return render_pdf_pages(data, page_list or None)
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
