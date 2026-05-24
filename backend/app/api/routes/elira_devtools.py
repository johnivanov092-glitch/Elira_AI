from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.application.elira_devtools import runtime as devtools_runtime

router = APIRouter(prefix="/api/elira", tags=["elira-devtools"])

ERROR_DETAILS = {
    "outside_root": (403, "Path is outside project root"),
    "blocked": (403, "Path points to blocked area"),
    "already_exists": (409, "Target already exists"),
    "not_found": (404, "Target not found"),
    "is_directory": (400, "Only file delete is supported"),
    "source_not_found": (404, "Source not found"),
    "target_exists": (409, "Target already exists"),
}


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


def raise_runtime_error(error: str | None) -> None:
    if not error:
        return
    status_code, detail = ERROR_DETAILS.get(error, (400, error))
    raise HTTPException(status_code=status_code, detail=detail)


@router.get("/project/map")
def project_map(limit: int = 300):
    return devtools_runtime.build_project_map(limit)


@router.post("/fs/create")
def fs_create(payload: FsCreatePayload):
    result, error = devtools_runtime.fs_create(payload.path, payload.content)
    raise_runtime_error(error)
    return result


@router.post("/fs/delete")
def fs_delete(payload: FsDeletePayload):
    result, error = devtools_runtime.fs_delete(payload.path)
    raise_runtime_error(error)
    return result


@router.post("/fs/rename")
def fs_rename(payload: FsRenamePayload):
    result, error = devtools_runtime.fs_rename(payload.old_path, payload.new_path)
    raise_runtime_error(error)
    return result


@router.post("/patch/plan")
def patch_plan(payload: PatchPlanPayload):
    return devtools_runtime.build_patch_plan(
        goal=payload.goal,
        current_path=payload.current_path,
        staged_paths=payload.staged_paths,
    )
