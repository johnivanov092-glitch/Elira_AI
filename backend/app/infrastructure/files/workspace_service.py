"""workspace_service.py — safe file operations inside the user workspace sandbox."""
from __future__ import annotations

import difflib
import shutil
from pathlib import Path

from fastapi import HTTPException

from app.core.config import DATA_DIR

WORKSPACE = DATA_DIR / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

BLOCKED = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}
MAX_FILE_SIZE = 500_000  # 500KB


def safe_path(rel_path: str) -> Path:
    rel = rel_path.strip().strip("/\\")
    if not rel:
        raise HTTPException(400, "Пустой путь")
    if any(part in BLOCKED for part in Path(rel).parts):
        raise HTTPException(400, f"Заблокированный путь: {rel}")
    full = (WORKSPACE / rel).resolve()
    if not str(full).startswith(str(WORKSPACE.resolve())):
        raise HTTPException(400, "Выход за пределы workspace")
    return full


def write_file(rel_path: str, content: str, create_dirs: bool = True) -> dict:
    full = safe_path(rel_path)
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"Файл слишком большой: {len(content)} > {MAX_FILE_SIZE}")
    if create_dirs:
        full.parent.mkdir(parents=True, exist_ok=True)
    existed = full.exists()
    old_size = None
    if existed:
        try:
            old_size = len(full.read_text(encoding="utf-8"))
        except Exception:
            pass
    full.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "path": rel_path,
        "action": "updated" if existed else "created",
        "size": len(content),
        "old_size": old_size,
    }


def read_file(rel_path: str, max_chars: int = 50000) -> dict:
    full = safe_path(rel_path)
    if not full.exists():
        raise HTTPException(404, f"Файл не найден: {rel_path}")
    if not full.is_file():
        raise HTTPException(400, f"Не файл: {rel_path}")
    try:
        content = full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = full.read_bytes().decode("utf-8", errors="replace")
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n... [обрезано, {len(content)} символов всего]"
    return {"ok": True, "path": rel_path, "content": content, "size": full.stat().st_size}


def file_tree(max_depth: int = 3, max_items: int = 200) -> dict:
    items: list[dict] = []

    def walk(dir_path: Path, depth: int) -> None:
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
                walk(entry, depth + 1)
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


def diff_file(rel_path: str, new_content: str) -> dict:
    full = safe_path(rel_path)
    old_content = ""
    if full.exists():
        try:
            old_content = full.read_text(encoding="utf-8")
        except Exception:
            pass
    diff_lines = list(difflib.unified_diff(
        old_content.splitlines(),
        new_content.splitlines(),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        lineterm="",
    ))
    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
    return {
        "ok": True,
        "path": rel_path,
        "changed": old_content != new_content,
        "diff": "\n".join(diff_lines),
        "stats": {"added": added, "removed": removed},
        "exists": full.exists(),
    }


def make_dir(rel_path: str) -> dict:
    full = safe_path(rel_path)
    full.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": rel_path}


def delete_path(rel_path: str) -> dict:
    full = safe_path(rel_path)
    if not full.exists():
        raise HTTPException(404, f"Не найден: {rel_path}")
    if full.is_dir():
        shutil.rmtree(full)
    else:
        full.unlink()
    return {"ok": True, "path": rel_path, "action": "deleted"}
