from __future__ import annotations

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.schemas.project_patch import (
    ApplyProjectPatchRequest,
    PreviewProjectPatchRequest,
    ReplaceInFileRequest,
)
from app.services.tool_service import run_tool

router = APIRouter(prefix="/api/project/patch", tags=["project-patch"])


@router.post("/preview")
def preview_project_patch(payload: PreviewProjectPatchRequest):
    try:
        result = run_tool(
            "preview_project_patch",
            {
                "path": payload.path,
                "new_content": payload.new_content,
                "max_chars": payload.max_chars,
            },
        )
        return JSONResponse(
            content=jsonable_encoder(result),
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        fallback = {
            "ok": False,
            "path": payload.path,
            "error": str(exc),
            "route": "/api/project/patch/preview",
        }
        return JSONResponse(
            content=jsonable_encoder(fallback),
            media_type="application/json; charset=utf-8",
        )


@router.post("/apply")
def apply_project_patch(payload: ApplyProjectPatchRequest):
    try:
        result = run_tool(
            "apply_project_patch",
            {
                "path": payload.path,
                "new_content": payload.new_content,
            },
        )
        return JSONResponse(
            content=jsonable_encoder(result),
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        fallback = {
            "ok": False,
            "path": payload.path,
            "error": str(exc),
            "route": "/api/project/patch/apply",
        }
        return JSONResponse(
            content=jsonable_encoder(fallback),
            media_type="application/json; charset=utf-8",
        )


@router.post("/replace")
def replace_in_file(payload: ReplaceInFileRequest):
    try:
        result = run_tool(
            "replace_in_file",
            {
                "path": payload.path,
                "old_text": payload.old_text,
                "new_text": payload.new_text,
                "max_chars": payload.max_chars,
            },
        )
        return JSONResponse(
            content=jsonable_encoder(result),
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        fallback = {
            "ok": False,
            "path": payload.path,
            "error": str(exc),
            "route": "/api/project/patch/replace",
        }
        return JSONResponse(
            content=jsonable_encoder(fallback),
            media_type="application/json; charset=utf-8",
        )
