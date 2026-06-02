"""
skills_routes.py — API скиллов: генерация файлов, SQL, HTTP, скриншоты.
"""
from __future__ import annotations
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import GENERATED_DIR
from app.application.skills import (
    generate_word, generate_excel,
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

@router.get("/download/{filename}")
def download_file(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        return {"ok": False, "error": f"Не найден: {filename}"}
    return FileResponse(path, filename=filename, media_type="application/octet-stream")

@router.get("/view/{filename}")
def view_file(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        return {"ok": False, "error": f"Не найден: {filename}"}
    mt = "image/png" if filename.endswith(".png") else "application/octet-stream"
    return FileResponse(path, media_type=mt)

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


# ── Skill catalog ──────────────────────────────────────────────────────────────

@router.get("/catalog", summary="Discover available skills (manifests only, no full content)")
def api_catalog(enabled_only: bool = True):
    from app.application.skills.catalog import discover_skills
    manifests = discover_skills(enabled_only=enabled_only)
    return {
        "skills": [
            {
                "id": m.id,
                "name": m.name,
                "description_short": m.description_short,
                "capabilities": m.capabilities,
                "trigger_words": m.trigger_words,
                "enabled": m.enabled,
            }
            for m in manifests
        ],
        "total": len(manifests),
    }


# NOTE: /catalog/match MUST be defined before /catalog/{skill_id}
# so FastAPI doesn't treat "match" as a skill_id.
@router.get("/catalog/match", summary="Match skills by trigger words in text")
def api_match_skills(text: str, top_k: int = 3):
    from app.application.skills.catalog import match_skills_by_trigger
    matches = match_skills_by_trigger(text, top_k=top_k)
    return {
        "matches": [
            {"id": m.id, "name": m.name, "description_short": m.description_short}
            for m in matches
        ],
        "count": len(matches),
    }


@router.get("/catalog/{skill_id}", summary="Get full skill content by id")
def api_skill_content(skill_id: str):
    from fastapi import HTTPException
    from app.application.skills.catalog import load_skill_content, discover_skills
    content = load_skill_content(skill_id)
    if content is None:
        raise HTTPException(404, f"Skill '{skill_id}' not found")
    manifests = {m.id: m for m in discover_skills(enabled_only=False)}
    m = manifests.get(skill_id)
    return {
        "id": skill_id,
        "name": m.name if m else skill_id,
        "content": content,
        "capabilities": m.capabilities if m else [],
        "enabled": m.enabled if m else False,
    }
