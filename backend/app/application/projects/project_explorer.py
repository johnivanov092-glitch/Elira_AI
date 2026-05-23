"""Project explorer — open/browse/search project files.

Extracted from api/routes/advanced_routes.py project mode section.
Holds the single process-level open project path and all file operations.
"""
from __future__ import annotations

from pathlib import Path

_project_path: str = ""

BLOCKED_DIRS = {
    ".git", "node_modules", ".venv", "__pycache__",
    "dist", "build", ".next", ".cache", "target", ".idea", ".vs",
}
TEXT_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".json",
    ".yml", ".yaml", ".toml", ".ini", ".md", ".txt", ".rs", ".go",
    ".java", ".c", ".cpp", ".h", ".sh", ".bat", ".sql", ".xml",
    ".csv", ".env", ".bas", ".vba", ".cls",
}


def open_project(path: str) -> dict:
    global _project_path
    p = Path(path).resolve()
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": f"Directory not found: {path}"}
    _project_path = str(p)
    return {"ok": True, "path": _project_path, "name": p.name}


def close_project() -> dict:
    global _project_path
    _project_path = ""
    return {"ok": True}


def project_info() -> dict:
    if not _project_path:
        return {"ok": False, "error": "No project open"}
    p = Path(_project_path)
    return {"ok": True, "path": _project_path, "name": p.name, "exists": p.exists()}


def project_tree(max_depth: int = 3, max_items: int = 300) -> dict:
    if not _project_path:
        return {"ok": False, "error": "No project open", "items": []}
    root = Path(_project_path)
    if not root.exists():
        return {"ok": False, "error": "Path does not exist", "items": []}

    items: list[dict] = []

    def walk(dir_path: Path, depth: int) -> None:
        if depth > max_depth or len(items) >= max_items:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.name.startswith(".") and entry.name not in (".env",):
                continue
            if entry.name in BLOCKED_DIRS:
                continue
            rel = str(entry.relative_to(root)).replace("\\", "/")
            if entry.is_dir():
                items.append({"path": rel, "type": "dir", "name": entry.name})
                walk(entry, depth + 1)
            else:
                items.append({
                    "path": rel, "type": "file", "name": entry.name,
                    "size": entry.stat().st_size, "ext": entry.suffix.lower(),
                })
            if len(items) >= max_items:
                return

    walk(root, 0)
    return {"ok": True, "items": items, "count": len(items), "root": _project_path}


def read_project_file(rel_path: str, max_chars: int = 20000) -> dict:
    if not _project_path:
        return {"ok": False, "error": "No project open"}
    full = (Path(_project_path) / rel_path).resolve()
    if not str(full).startswith(_project_path):
        return {"ok": False, "error": "Path escapes project root"}
    if not full.exists() or not full.is_file():
        return {"ok": False, "error": f"File not found: {rel_path}"}
    try:
        content = full.read_text(encoding="utf-8", errors="replace")[:max_chars]
        return {"ok": True, "path": rel_path, "content": content, "size": full.stat().st_size}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def search_project(query: str, max_results: int = 20) -> dict:
    if not _project_path:
        return {"ok": False, "error": "No project open"}
    root = Path(_project_path)
    q = query.lower()
    results: list[dict] = []

    for fpath in root.rglob("*"):
        if len(results) >= max_results:
            break
        if not fpath.is_file() or fpath.suffix.lower() not in TEXT_EXTS:
            continue
        rel = str(fpath.relative_to(root)).replace("\\", "/")
        if any(b in rel for b in BLOCKED_DIRS):
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(content.split("\n"), 1):
                if q in line.lower():
                    results.append({"path": rel, "line": i, "text": line.strip()[:200]})
                    if len(results) >= max_results:
                        break
        except Exception:
            continue

    return {"ok": True, "items": results, "count": len(results), "query": query}
