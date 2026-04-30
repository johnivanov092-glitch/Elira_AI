"""HTTP layer for the Elira devtools endpoints.

All business logic lives in
``app.application.elira_devtools.runtime``; this module keeps only the
FastAPI router, Pydantic request models, and the thin delegating handlers
that translate runtime error kinds into HTTPException codes.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.application.elira_devtools import runtime as devtools_runtime

router = APIRouter(prefix="/api/elira", tags=["elira-devtools"])

# ── error kind -> (status, detail) ──────────────────────────────────────────
_PATH_ERRORS = {
    "outside_root": (403, "Path is outside project root"),
    "blocked":      (403, "Path points to blocked area"),
}
_FS_CREATE_ERRORS = {
    **_PATH_ERRORS,
    "already_exists": (409, "Target already exists"),
}
_FS_DELETE_ERRORS = {
    **_PATH_ERRORS,
    "not_found":    (404, "Target not found"),
    "is_directory": (400, "Only file delete is supported"),
}
_FS_RENAME_ERRORS = {
    **_PATH_ERRORS,
    "source_not_found": (404, "Source not found"),
    "target_exists":    (409, "Target already exists"),
}


def _raise(errors: dict, kind: str) -> None:
    status, detail = errors.get(kind, (400, "Invalid path"))
    raise HTTPException(status_code=status, detail=detail)


# ── Pydantic models ──────────────────────────────────────────────────────────

class FsCreatePayload(BaseModel):
    path: str = Field(min_length=1)
    content: str = ""


class FsDeletePayload(BaseModel):
    path: str = Field(min_length=1)


class FsRenamePayload(BaseModel):
    old_path: str = Field(min_length=1)
    new_path: str = Field(min_length=1)


class PatchPlanPayload(BaseModel):
    goal: str = Field(min_length=1)
    current_path: Optional[str] = None
    current_content: Optional[str] = None
    staged_paths: List[str] = []


# ── Handlers ─────────────────────────────────────────────────────────────────

@router.get("/project/map")
def project_map(limit: int = 300):
    return devtools_runtime.build_project_map(limit)


@router.post("/fs/create")
def fs_create(payload: FsCreatePayload):
    result, err = devtools_runtime.fs_create(payload.path, payload.content)
    if err:
        _raise(_FS_CREATE_ERRORS, err)
    return result


@router.post("/fs/delete")
def fs_delete(payload: FsDeletePayload):
    result, err = devtools_runtime.fs_delete(payload.path)
    if err:
        _raise(_FS_DELETE_ERRORS, err)
    return result


@router.post("/fs/rename")
def fs_rename(payload: FsRenamePayload):
    result, err = devtools_runtime.fs_rename(payload.old_path, payload.new_path)
    if err:
        _raise(_FS_RENAME_ERRORS, err)
    return result


@router.post("/patch/plan")
def patch_plan(payload: PatchPlanPayload):
    return devtools_runtime.build_patch_plan(
        payload.goal,
        payload.current_path,
        payload.staged_paths,
    )
