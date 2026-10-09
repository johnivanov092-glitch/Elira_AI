"""
skills_routes.py — доставка файлов, SQL, HTTP и скриншоты.
"""
from __future__ import annotations
import mimetypes
import logging
import html
from pathlib import Path
from urllib.parse import quote
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.core.config import GENERATED_DIR
from app.application.skills import (
    run_sql, list_databases, describe_db,
)

router = APIRouter(prefix="/api/skills", tags=["skills"])
OUTPUT_DIR = GENERATED_DIR
logger = logging.getLogger(__name__)


def _published_path(filename: str) -> Path:
    """Only a regular published file, never a directory or an escaping link/path."""
    path = OUTPUT_DIR / filename
    try:
        valid = (filename not in {"", ".", ".."} and not any(c in filename for c in ("/", "\\", ":", "\x00"))
                 and path.resolve().parent == OUTPUT_DIR.resolve() and path.is_file())
    except (OSError, RuntimeError, ValueError):
        valid = False
    if not valid:
        raise HTTPException(status_code=404, detail=f"Не найден: {filename}")
    return path


@router.get("/download/{filename}")
def download_file(filename: str):
    path = _published_path(filename)
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path, filename=filename, media_type=media_type)

@router.get("/view/{filename}")
def view_file(filename: str, pages: bool = False, page: int | None = None, version: str = ""):
    path = _published_path(filename)
    if (pages or page is not None) and path.suffix.lower() not in {".pdf", ".docx"}:
        raise HTTPException(status_code=415, detail="Превью страниц доступно только для PDF и DOCX.")
    if path.suffix.lower() == ".docx" or pages or page is not None:
        from app.application.skill_services import documents

        try:
            preview = documents.document_preview(path, OUTPUT_DIR / ".previews")
            if page is not None:
                if version != preview.stem:
                    raise HTTPException(status_code=409, detail="Файл изменился. Откройте превью заново.")
                count = documents._page_count(preview)
                if page < 1 or count is None or page > count:
                    raise HTTPException(status_code=404, detail="Страница не найдена.")
                image = documents.document_page_preview(preview, page)
                return FileResponse(image, media_type="image/png")
            if pages:
                count = documents._page_count(preview)
                if count is None or not 1 <= count <= 1000:
                    raise RuntimeError("Preview page count is unavailable or exceeds 1000")
                url = f"/api/skills/view/{quote(filename, safe='')}"
                figures = "".join(
                    f'<figure><figcaption>Страница {number} из {count}</figcaption>'
                    f'<img loading="lazy" alt="Страница {number} документа" '
                    f'src="{url}?page={number}&amp;version={preview.stem}"></figure>'
                    for number in range(1, count + 1)
                )
                return HTMLResponse(
                    '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    f'<title>{html.escape(filename)}</title>'
                    '<style>body{margin:0;background:#27272a;color:#eee;font:13px system-ui}'
                    'figure{margin:12px}figcaption{margin:8px 0}img{display:block;width:100%;'
                    'height:auto;background:white;min-height:80px}</style></head>'
                    f'<body>{figures}</body></html>',
                    headers={"Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
                             "Cache-Control": "no-store"},
                )
        except (OSError, RuntimeError, ImportError, AttributeError) as exc:
            logger.warning("Document preview failed for %s: %s", filename, exc)
            raise HTTPException(status_code=503, detail="Превью пока недоступно. Исходный файл можно скачать.") from exc
        return FileResponse(preview, media_type="application/pdf")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)

@router.get("/files")
def list_generated():
    if not OUTPUT_DIR.exists():
        return {"ok": True, "files": []}
    files = [{"name": f.name, "size": f.stat().st_size, "download_url": f"/api/skills/download/{quote(f.name, safe='')}"}
             for f in sorted(OUTPUT_DIR.iterdir()) if f.is_file() and f.resolve().parent == OUTPUT_DIR.resolve()]
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
    raise HTTPException(status_code=410, detail="Use data/skills/web-research")


# ── Скриншот ──

class ScreenshotRequest(BaseModel):
    url: str
    width: int = 1280
    height: int = 800
    full_page: bool = False

@router.post("/screenshot")
def api_screenshot(payload: ScreenshotRequest):
    raise HTTPException(status_code=410, detail="Use data/skills/web-research")
