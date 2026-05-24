"""
file_ops.py - файловые операции для патчинга из чата.

Эндпоинты:
  POST /api/file-ops/write     - создать/перезаписать файл
  POST /api/file-ops/read      - прочитать файл
  GET  /api/file-ops/tree      - дерево файлов в workspace
  POST /api/file-ops/diff      - показать diff между old и new
  POST /api/file-ops/mkdir     - создать директорию
  DELETE /api/file-ops/delete   - удалить файл

Workspace = data/workspace/ (безопасная песочница)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.application.file_ops import runtime as file_ops_runtime

router = APIRouter(prefix="/api/file-ops", tags=["file-ops"])

ERROR_STATUS = {
    "not_found": 404,
}


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


def raise_runtime_error(error: dict[str, str] | None) -> None:
    if not error:
        return
    kind = error.get("kind", "")
    detail = error.get("detail") or kind
    raise HTTPException(ERROR_STATUS.get(kind, 400), detail)


@router.post("/write")
def write_file(payload: WriteRequest):
    result, error = file_ops_runtime.write_file(payload.path, payload.content, payload.create_dirs)
    raise_runtime_error(error)
    return result


@router.post("/read")
def read_file(payload: ReadRequest):
    result, error = file_ops_runtime.read_file(payload.path, payload.max_chars)
    raise_runtime_error(error)
    return result


@router.get("/tree")
def file_tree(max_depth: int = 3, max_items: int = 200):
    return file_ops_runtime.file_tree(max_depth, max_items)


@router.post("/diff")
def diff_file(payload: DiffRequest):
    result, error = file_ops_runtime.diff_file(payload.path, payload.new_content)
    raise_runtime_error(error)
    return result


@router.post("/mkdir")
def mkdir(payload: MkdirRequest):
    result, error = file_ops_runtime.mkdir(payload.path)
    raise_runtime_error(error)
    return result


@router.delete("/delete")
def delete_file(payload: DeleteRequest):
    result, error = file_ops_runtime.delete_path(payload.path)
    raise_runtime_error(error)
    return result
