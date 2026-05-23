"""
file_ops.py — workspace file operation routes (thin HTTP layer).

All logic lives in app.infrastructure.files.workspace_service.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.infrastructure.files.workspace_service import (
    write_file,
    read_file,
    file_tree,
    diff_file,
    make_dir,
    delete_path,
)

router = APIRouter(prefix="/api/file-ops", tags=["file-ops"])


class WriteRequest(BaseModel):
    path: str = Field(min_length=1)
    content: str
    create_dirs: bool = True


class ReadRequest(BaseModel):
    path: str = Field(min_length=1)
    max_chars: int = 50000


class DiffRequest(BaseModel):
    path: str = Field(min_length=1)
    new_content: str


class MkdirRequest(BaseModel):
    path: str = Field(min_length=1)


class DeleteRequest(BaseModel):
    path: str = Field(min_length=1)


@router.post("/write")
def api_write_file(payload: WriteRequest):
    return write_file(payload.path, payload.content, payload.create_dirs)


@router.post("/read")
def api_read_file(payload: ReadRequest):
    return read_file(payload.path, payload.max_chars)


@router.get("/tree")
def api_file_tree(max_depth: int = 3, max_items: int = 200):
    return file_tree(max_depth, max_items)


@router.post("/diff")
def api_diff_file(payload: DiffRequest):
    return diff_file(payload.path, payload.new_content)


@router.post("/mkdir")
def api_mkdir(payload: MkdirRequest):
    return make_dir(payload.path)


@router.delete("/delete")
def api_delete_file(payload: DeleteRequest):
    return delete_path(payload.path)
