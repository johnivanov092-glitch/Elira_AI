"""
file_ops.py — файловые операции для патчинга из чата.

Эндпоинты:
  POST /api/file-ops/write     — создать/перезаписать файл
  POST /api/file-ops/read      — прочитать файл
  GET  /api/file-ops/tree      — дерево файлов в workspace
  POST /api/file-ops/diff      — показать diff между old и new
  POST /api/file-ops/mkdir     — создать директорию
  DELETE /api/file-ops/delete   — удалить файл

Workspace = data/workspace/ (безопасная песочница)
"""
from __future__ import annotations

import difflib
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import DATA_DIR

router = APIRouter(prefix="/api/file-ops", tags=["file-ops"])

# Workspace — безопасная папка для пользовательских файлов
WORKSPACE = DATA_DIR / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

BLOCKED = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}
MAX_FILE_SIZE = 500_000  # 500KB


def _safe_path(rel_path: str) -> Path:
    """Нормализует путь и проверяет что он внутри workspace."""
    rel = rel_path.strip().strip("/\\")
    if not rel:
        raise HTTPException(400, "Пустой путь")
    if any(part in BLOCKED for part in Path(rel).parts):
        raise HTTPException(400, f"Заблокированный путь: {rel}")
    full = (WORKSPACE / rel).resolve()
    if not str(full).startswith(str(WORKSPACE.resolve())):
        raise HTTPException(400, "Выход за пределы workspace")
    return full


# ── Models ──

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


# ── Endpoints ──

@router.post("/write")
def write_file(payload: WriteRequest):
    """Создаёт или перезаписывает файл."""
    full = _safe_path(payload.path)
    content = payload.content or ""

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"Файл слишком большой: {len(content)} > {MAX_FILE_SIZE}")

    if payload.create_dirs:
        full.parent.mkdir(parents=True, exist_ok=True)

    existed = full.exists()
    old_content = ""
    if existed:
        try:
            old_content = full.read_text(encoding="utf-8")
        except Exception:
            pass

    full.write_text(content, encoding="utf-8")

    return {
        "ok": True,
        "path": payload.path,
        "action": "updated" if existed else "created",
        "size": len(content),
        "old_size": len(old_content) if existed else None,
    }


@router.post("/read")
def read_file(payload: ReadRequest):
    """Читает файл из workspace."""
    full = _safe_path(payload.path)
    if not full.exists():
        raise HTTPException(404, f"Файл не найден: {payload.path}")
    if not full.is_file():
        raise HTTPException(400, f"Не файл: {payload.path}")

    try:
        content = full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = full.read_bytes().decode("utf-8", errors="replace")

    if len(content) > payload.max_chars:
        content = content[:payload.max_chars] + f"\n\n... [обрезано, {len(content)} символов всего]"

    return {
        "ok": True,
        "path": payload.path,
        "content": content,
        "size": full.stat().st_size,
    }


@router.get("/tree")
def file_tree(max_depth: int = 3, max_items: int = 200):
    """Дерево файлов в workspace."""
    items = []

    def walk(dir_path: Path, depth: int, prefix: str = ""):
        if depth > max_depth or len(items) >= max_items:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return

        for entry in entries:
            if entry.name.startswith(".") or entry.name in BLOCKED:
                continue
            rel = str(entry.relative_to(WORKSPACE)).replace("\\", "/")
            if entry.is_dir():
                items.append({"path": rel, "type": "dir", "name": entry.name})
                walk(entry, depth + 1, rel + "/")
            else:
                items.append({
                    "path": rel,
                    "type": "file",
                    "name": entry.name,
                    "size": entry.stat().st_size,
                    "ext": entry.suffix.lower(),
                })
            if len(items) >= max_items:
                return

    walk(WORKSPACE, 0)
    return {"ok": True, "items": items, "count": len(items), "workspace": str(WORKSPACE.resolve())}


@router.post("/diff")
def diff_file(payload: DiffRequest):
    """Показывает diff между текущим файлом и новым содержимым."""
    full = _safe_path(payload.path)
    old_content = ""
    if full.exists():
        try:
            old_content = full.read_text(encoding="utf-8")
        except Exception:
            pass

    diff_lines = list(difflib.unified_diff(
        old_content.splitlines(),
        payload.new_content.splitlines(),
        fromfile=f"a/{payload.path}",
        tofile=f"b/{payload.path}",
        lineterm="",
    ))

    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    return {
        "ok": True,
        "path": payload.path,
        "changed": old_content != payload.new_content,
        "diff": "\n".join(diff_lines),
        "stats": {"added": added, "removed": removed},
        "exists": full.exists(),
    }


@router.post("/mkdir")
def mkdir(payload: MkdirRequest):
    """Создаёт директорию."""
    full = _safe_path(payload.path)
    full.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": payload.path}


@router.delete("/delete")
def delete_file(payload: DeleteRequest):
    """Удаляет файл."""
    full = _safe_path(payload.path)
    if not full.exists():
        raise HTTPException(404, f"Не найден: {payload.path}")
    if full.is_dir():
        shutil.rmtree(full)
    else:
        full.unlink()
    return {"ok": True, "path": payload.path, "action": "deleted"}
