"""
files.py — file text extraction route (thin HTTP layer).

All extraction logic lives in app.infrastructure.files.file_extractor.
"""
from pathlib import Path

from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse

from app.infrastructure.files.file_extractor import extract_any

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/extract-text")
async def extract_text(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        filename = (file.filename or "").strip()
        text = extract_any(contents, filename)
        return {
            "ok": True,
            "filename": filename,
            "size": len(contents),
            "text": text,
            "chars": len(text),
            "type": Path(filename).suffix.lower(),
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
