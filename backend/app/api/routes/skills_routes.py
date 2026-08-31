"""
skills_routes.py — API скиллов: генерация файлов, SQL, HTTP, скриншоты.
"""
from __future__ import annotations
import mimetypes
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import GENERATED_DIR
from app.application.skills import (
    generate_word, generate_excel, generate_pdf,
    run_sql, list_databases, describe_db,
    http_request, screenshot_url,
)

router = APIRouter(prefix="/api/skills", tags=["skills"])
OUTPUT_DIR = GENERATED_DIR


# ── Генерация файлов ──

class WordRequest(BaseModel):
    title: str = ""
    content: str
    filename: str = ""

class ExcelRequest(BaseModel):
    title: str = "Sheet1"
    headers: list[str] = []
    data: list[list[Any]] = []
    filename: str = ""

@router.post("/generate/word")
def api_word(payload: WordRequest):
    return generate_word(payload.title, payload.content, payload.filename)

@router.post("/generate/excel")
def api_excel(payload: ExcelRequest):
    return generate_excel(payload.title, payload.data, payload.headers, payload.filename)

class PdfRequest(BaseModel):
    title: str = ""
    content: str
    filename: str = ""

@router.post("/generate/pdf")
def api_pdf(payload: PdfRequest):
    return generate_pdf(payload.title, payload.content, payload.filename)

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
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Не найден: {filename}")
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
