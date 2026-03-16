from __future__ import annotations

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.schemas.project_patch import (
    ApplyProjectPatchRequest,
    PreviewProjectPatchRequest,
    ReplaceInFileRequest,
    RollbackProjectPatchRequest,
    PatchBackupsRequest,
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
        return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
    except Exception as exc:
        return JSONResponse(
            content=jsonable_encoder({
                "ok": False,
                "path": payload.path,
                "error": str(exc),
                "route": "/api/project/patch/preview",
            }),
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
        return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
    except Exception as exc:
        return JSONResponse(
            content=jsonable_encoder({
                "ok": False,
                "path": payload.path,
                "error": str(exc),
                "route": "/api/project/patch/apply",
            }),
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
        return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
    except Exception as exc:
        return JSONResponse(
            content=jsonable_encoder({
                "ok": False,
                "path": payload.path,
                "error": str(exc),
                "route": "/api/project/patch/replace",
            }),
            media_type="application/json; charset=utf-8",
        )


@router.post("/replace-apply")
def apply_replace_in_file(payload: ReplaceInFileRequest):
    try:
        result = run_tool(
            "apply_replace_in_file",
            {
                "path": payload.path,
                "old_text": payload.old_text,
                "new_text": payload.new_text,
                "max_chars": payload.max_chars,
            },
        )
        return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
    except Exception as exc:
        return JSONResponse(
            content=jsonable_encoder({
                "ok": False,
                "path": payload.path,
                "error": str(exc),
                "route": "/api/project/patch/replace-apply",
            }),
            media_type="application/json; charset=utf-8",
        )


@router.post("/rollback")
def rollback_project_patch(payload: RollbackProjectPatchRequest):
    try:
        result = run_tool(
            "rollback_project_patch",
            {
                "path": payload.path,
                "backup_id": payload.backup_id,
            },
        )
        return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
    except Exception as exc:
        return JSONResponse(
            content=jsonable_encoder({
                "ok": False,
                "path": payload.path,
                "error": str(exc),
                "route": "/api/project/patch/rollback",
            }),
            media_type="application/json; charset=utf-8",
        )


@router.post("/backups")
def list_project_patch_backups(payload: PatchBackupsRequest):
    try:
        result = run_tool(
            "list_patch_backups",
            {
                "path": payload.path,
                "limit": payload.limit,
            },
        )
        return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
    except Exception as exc:
        return JSONResponse(
            content=jsonable_encoder({
                "ok": False,
                "path": payload.path,
                "error": str(exc),
                "route": "/api/project/patch/backups",
            }),
            media_type="application/json; charset=utf-8",
        )
