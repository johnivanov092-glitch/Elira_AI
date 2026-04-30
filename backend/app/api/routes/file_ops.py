"""HTTP layer for the Elira workspace file-ops endpoints.

All business logic lives in
``app.application.file_ops.runtime``; this module keeps only the
FastAPI router, Pydantic request models, and the thin delegating handlers
that translate runtime error kinds into HTTPException codes.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.application.file_ops import runtime as file_ops_runtime

router = APIRouter(prefix="/api/file-ops", tags=["file-ops"])

# ── error kind -> (status, detail) ──────────────────────────────────────────
_PATH_ERRORS = {
    "empty":              (400, "Empty path"),
    "blocked":            (400, "Blocked path"),
    "outside_workspace":  (400, "Path outside workspace"),
}
_WRITE_ERRORS = {
    **_PATH_ERRORS,
    "too_large": (400, f"File too large (max {file_ops_runtime.MAX_FILE_SIZE} bytes)"),
}
_READ_ERRORS = {
    **_PATH_ERRORS,
    "not_found":  (404, "File not found"),
    "not_a_file": (400, "Path is not a file"),
}
_DELETE_ERRORS = {
    **_PATH_ERRORS,
    "not_found": (404, "Path not found"),
}


def _raise(errors: dict, kind: str) -> None:
    status, detail = errors.get(kind, (400, "Invalid path"))
    raise HTTPException(status_code=status, detail=detail)


# ── Pydantic models ──────────────────────────────────────────────────────────

class WriteRequest(BaseModel):
    path: str = Field(min_length=1)
    content: str = ""
    create_dirs: bool = True


class ReadRequest(BaseModel):
    path: str = Field(min_length=1)
    max_chars: int = 50000


class DiffRequest(BaseModel):
    path: str = Field(min_length=1)
    new_content: str = ""


class MkdirRequest(BaseModel):
    path: str = Field(min_length=1)


class DeleteRequest(BaseModel):
    path: str = Field(min_length=1)


# ── Handlers ─────────────────────────────────────────────────────────────────

@router.post("/write")
def write_file(payload: WriteRequest):
    result, err = file_ops_runtime.write_file(payload.path, payload.content, payload.create_dirs)
    if err:
        _raise(_WRITE_ERRORS, err)
    return result


@router.post("/read")
def read_file(payload: ReadRequest):
    result, err = file_ops_runtime.read_file(payload.path, payload.max_chars)
    if err:
        _raise(_READ_ERRORS, err)
    return result


@router.get("/tree")
def file_tree(max_depth: int = 3, max_items: int = 200):
    return file_ops_runtime.file_tree(max_depth, max_items)


@router.post("/diff")
def diff_file(payload: DiffRequest):
    result, err = file_ops_runtime.diff_file(payload.path, payload.new_content)
    if err:
        _raise(_PATH_ERRORS, err)
    return result


@router.post("/mkdir")
def mkdir(payload: MkdirRequest):
    result, err = file_ops_runtime.mkdir_dir(payload.path)
    if err:
        _raise(_PATH_ERRORS, err)
    return result


@router.delete("/delete")
def delete_file(payload: DeleteRequest):
    result, err = file_ops_runtime.delete_path(payload.path)
    if err:
        _raise(_DELETE_ERRORS, err)
    return result
