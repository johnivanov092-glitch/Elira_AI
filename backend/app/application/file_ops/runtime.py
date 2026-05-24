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
import shutil
from pathlib import Path

from app.core.config import DATA_DIR

# Workspace — безопасная папка для пользовательских файлов
WORKSPACE = DATA_DIR / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

BLOCKED = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}
MAX_FILE_SIZE = 500_000  # 500KB


def _runtime_error(kind: str, detail: str) -> dict[str, str]:
    return {"kind": kind, "detail": detail}


def safe_path(rel_path: str) -> tuple[Path | None, dict[str, str] | None]:
    """Нормализует путь и проверяет что он внутри workspace."""
    rel = rel_path.strip().strip("/\\")
    if not rel:
        return None, _runtime_error("empty_path", "Пустой путь")
    if any(part in BLOCKED for part in Path(rel).parts):
        return None, _runtime_error("blocked_path", f"Заблокированный путь: {rel}")
    full = (WORKSPACE / rel).resolve()
    if not str(full).startswith(str(WORKSPACE.resolve())):
        return None, _runtime_error("outside_workspace", "Выход за пределы workspace")
    return full, None


def write_file(rel_path: str, content: str, create_dirs: bool = True):
    """Создаёт или перезаписывает файл."""
    full, error = safe_path(rel_path)
    if error:
        return None, error
    content = content or ""

    if len(content) > MAX_FILE_SIZE:
        return None, _runtime_error("too_large", f"Файл слишком большой: {len(content)} > {MAX_FILE_SIZE}")

    if create_dirs:
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
        "path": rel_path,
        "action": "updated" if existed else "created",
        "size": len(content),
        "old_size": len(old_content) if existed else None,
    }, None


def read_file(rel_path: str, max_chars: int = 50000):
    """Читает файл из workspace."""
    full, error = safe_path(rel_path)
    if error:
        return None, error
    if not full.exists():
        return None, _runtime_error("not_found", f"Файл не найден: {rel_path}")
    if not full.is_file():
        return None, _runtime_error("not_a_file", f"Не файл: {rel_path}")

    try:
        content = full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = full.read_bytes().decode("utf-8", errors="replace")

    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n... [обрезано, {len(content)} символов всего]"

    return {
        "ok": True,
        "path": rel_path,
        "content": content,
        "size": full.stat().st_size,
    }, None


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


def diff_file(rel_path: str, new_content: str):
    """Показывает diff между текущим файлом и новым содержимым."""
    full, error = safe_path(rel_path)
    if error:
        return None, error
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

    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    return {
        "ok": True,
        "path": rel_path,
        "changed": old_content != new_content,
        "diff": "\n".join(diff_lines),
        "stats": {"added": added, "removed": removed},
        "exists": full.exists(),
    }, None


def mkdir(rel_path: str):
    """Создаёт директорию."""
    full, error = safe_path(rel_path)
    if error:
        return None, error
    full.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": rel_path}, None


def delete_path(rel_path: str):
    """Удаляет файл."""
    full, error = safe_path(rel_path)
    if error:
        return None, error
    if not full.exists():
        return None, _runtime_error("not_found", f"Не найден: {rel_path}")
    if full.is_dir():
        shutil.rmtree(full)
    else:
        full.unlink()
    return {"ok": True, "path": rel_path, "action": "deleted"}, None
