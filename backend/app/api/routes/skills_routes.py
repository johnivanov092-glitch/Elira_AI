"""
skills_routes.py — доставка файлов, SQL, HTTP и скриншоты.
"""
from __future__ import annotations
import mimetypes
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import GENERATED_DIR
from app.application.skills import (
    run_sql, list_databases, describe_db,
    http_request, screenshot_url,
)

router = APIRouter(prefix="/api/skills", tags=["skills"])
OUTPUT_DIR = GENERATED_DIR
logger = logging.getLogger(__name__)


@router.get("/download/{filename}")
def download_file(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Не найден: {filename}")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path, filename=filename, media_type=media_type)

@router.get("/view/{filename}")
def view_file(filename: str):
    path = OUTPUT_DIR / filename
    if path.resolve().parent != OUTPUT_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Не найден: {filename}")
    if path.suffix.lower() == ".docx":
        from app.application.code_agent.document_validation import document_preview

        try:
            preview = document_preview(path, OUTPUT_DIR / ".previews")
        except (OSError, RuntimeError) as exc:
            logger.warning("DOCX preview failed for %s: %s", filename, exc)
            raise HTTPException(status_code=503, detail="Превью пока недоступно. Исходный DOCX можно скачать.") from exc
        return FileResponse(preview, media_type="application/pdf")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)

@router.get("/files")
def list_generated():
    if not OUTPUT_DIR.exists():
        return {"ok": True, "files": []}
    files = [{"name": f.name, "size": f.stat().st_size, "download_url": f"/api/skills/download/{f.name}"}
             for f in sorted(OUTPUT_DIR.iterdir()) if f.is_file()]
    return {"ok": True, "files": files, "count": len(files)}


# ── SQL ──

class SqlRequest(BaseModel):
    db_path: str
    query: str
    params: list = []
    max_rows: int = 100

@router.post("/sql/query")
def api_sql(payload: SqlRequest):
    return run_sql(payload.db_path, payload.query, payload.params, payload.max_rows)

@router.get("/sql/databases")
def api_list_dbs():
    return list_databases()

@router.post("/sql/describe")
def api_describe(db_path: str = ""):
    return describe_db(db_path)


# ── HTTP / API ──

class HttpRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict = {}
    body: Any = None
    timeout: int = 15

@router.post("/http")
def api_http(payload: HttpRequest):
    return http_request(payload.url, payload.method, payload.headers, payload.body, payload.timeout)


# ── Скриншот ──

class ScreenshotRequest(BaseModel):
    url: str
    width: int = 1280
    height: int = 800
    full_page: bool = False

@router.post("/screenshot")
def api_screenshot(payload: ScreenshotRequest):
    return screenshot_url(payload.url, payload.width, payload.height, payload.full_page)
