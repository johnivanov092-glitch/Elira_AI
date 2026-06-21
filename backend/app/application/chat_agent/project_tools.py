from __future__ import annotations

from pathlib import Path
from typing import Any


BLOCKED_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules", "dist", "build", "target"}
TEXT_EXTS = {
    ".bat", ".cmd", ".css", ".csv", ".env", ".html", ".ini", ".js", ".json", ".jsx", ".log",
    ".md", ".ps1", ".py", ".rs", ".sh", ".sql", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}


def resolve_project_root(path: str | None) -> Path | None:
    if not path:
        return None
    root = Path(path).expanduser().resolve()
    return root if root.exists() and root.is_dir() else None


def _safe_child(root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    target = candidate if candidate.is_absolute() else root / candidate
    resolved = target.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def _is_blocked(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in BLOCKED_DIRS for part in rel.parts)


def project_tree(root_path: str, *, max_depth: int = 3, max_items: int = 300) -> dict[str, Any]:
    root = resolve_project_root(root_path)
    if root is None:
        return {"ok": False, "error": "project root does not exist", "items": []}
    items: list[dict[str, Any]] = []
    base_depth = len(root.parts)
    for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        if len(items) >= max_items:
            break
        if _is_blocked(path, root):
            continue
        depth = len(path.parts) - base_depth
        if depth > max_depth:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        items.append({
            "name": path.name,
            "path": rel,
            "type": "dir" if path.is_dir() else "file",
            "ext": path.suffix.lower() if path.is_file() else "",
            "size": path.stat().st_size if path.is_file() else 0,
        })
    return {"ok": True, "root": str(root), "count": len(items), "items": items}


def read_project_file(root_path: str, rel_path: str, *, max_chars: int = 20000) -> dict[str, Any]:
    root = resolve_project_root(root_path)
    if root is None:
        return {"ok": False, "error": "project root does not exist", "content": ""}
    try:
        target = _safe_child(root, rel_path)
    except ValueError:
        return {"ok": False, "error": "file is outside project root", "content": ""}
    if not target.exists() or not target.is_file() or _is_blocked(target, root):
        return {"ok": False, "error": "file not found", "content": ""}
    if target.suffix.lower() not in TEXT_EXTS:
        return {"ok": False, "error": "binary or unsupported file type", "content": ""}
    text = target.read_text(encoding="utf-8", errors="replace")
    return {"ok": True, "path": str(target.relative_to(root)).replace("\\", "/"), "content": text[:max_chars]}


def search_project(root_path: str, query: str, *, max_results: int = 50) -> dict[str, Any]:
    root = resolve_project_root(root_path)
    needle = (query or "").strip().lower()
    if root is None:
        return {"ok": False, "error": "project root does not exist", "items": []}
    if not needle:
        return {"ok": True, "items": [], "count": 0}
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        if len(items) >= max_results:
            break
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTS or _is_blocked(path, root):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if needle in line.lower():
                items.append({
                    "path": str(path.relative_to(root)).replace("\\", "/"),
                    "line": line_no,
                    "text": line.strip()[:500],
                })
                break
    return {"ok": True, "root": str(root), "items": items, "count": len(items)}
