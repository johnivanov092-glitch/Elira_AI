"""Application-layer runtime for the Elira workspace file-ops endpoints.

Owns safe-path resolution, and all workspace FS operations (write, read,
tree, diff, mkdir, delete).  The HTTP layer in
``api/routes/file_ops.py`` is a thin FastAPI shell that translates
runtime error kinds into HTTPException codes.
"""
from __future__ import annotations

import difflib
import shutil
from pathlib import Path
from typing import Optional, Tuple

from app.core.config import DATA_DIR


WORKSPACE = DATA_DIR / "workspace"
BLOCKED = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}
MAX_FILE_SIZE = 500_000  # 500 KB

# Create workspace on module load
WORKSPACE.mkdir(parents=True, exist_ok=True)


# ───────── Path helper (HTTP-free) ─────────

def safe_path(rel_path: str) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve *rel_path* inside WORKSPACE.

    Returns ``(full_path, None)`` on success or ``(None, error_kind)`` on
    failure.  Error kinds: ``"empty"``, ``"blocked"``, ``"outside_workspace"``.
    """
    rel = rel_path.strip().strip("/\\")
    if not rel:
        return None, "empty"
    if any(part in BLOCKED for part in Path(rel).parts):
        return None, "blocked"
    full = (WORKSPACE / rel).resolve()
    if not str(full).startswith(str(WORKSPACE.resolve())):
        return None, "outside_workspace"
    return full, None


# ───────── FS operations ─────────

def write_file(
    rel_path: str,
    content: str,
    create_dirs: bool = True,
) -> Tuple[Optional[dict], Optional[str]]:
    full, err = safe_path(rel_path)
    if err:
        return None, err

    content = content or ""
    if len(content) > MAX_FILE_SIZE:
        return None, "too_large"

    assert full is not None
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


def read_file(
    rel_path: str,
    max_chars: int = 50000,
) -> Tuple[Optional[dict], Optional[str]]:
    full, err = safe_path(rel_path)
    if err:
        return None, err
    assert full is not None
    if not full.exists():
        return None, "not_found"
    if not full.is_file():
        return None, "not_a_file"

    try:
        content = full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = full.read_bytes().decode("utf-8", errors="replace")

    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n... [truncated, {len(content)} chars total]"

    return {
        "ok": True,
        "path": rel_path,
        "content": content,
        "size": full.stat().st_size,
    }, None


def file_tree(max_depth: int = 3, max_items: int = 200) -> dict:
    items: list = []

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


def diff_file(rel_path: str, new_content: str) -> Tuple[Optional[dict], Optional[str]]:
    full, err = safe_path(rel_path)
    if err:
        return None, err
    assert full is not None

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
    }, None


def mkdir_dir(rel_path: str) -> Tuple[Optional[dict], Optional[str]]:
    full, err = safe_path(rel_path)
    if err:
        return None, err
    assert full is not None
    full.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": rel_path}, None


def delete_path(rel_path: str) -> Tuple[Optional[dict], Optional[str]]:
    full, err = safe_path(rel_path)
    if err:
        return None, err
    assert full is not None
    if not full.exists():
        return None, "not_found"
    if full.is_dir():
        shutil.rmtree(full)
    else:
        full.unlink()
    return {"ok": True, "path": rel_path, "action": "deleted"}, None
