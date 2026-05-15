from __future__ import annotations

from pathlib import Path
from typing import Any
import os

BASE_DIR = Path(__file__).resolve().parents[3]

TEXT_EXTS = {
    ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".js", ".ts", ".tsx",
    ".jsx", ".html", ".css", ".sql", ".csv", ".toml", ".ini"
}

IGNORE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".vscode",
    "dist", "build", ".next", "coverage"
}


def _is_safe_path(path: Path) -> bool:
    try:
        path.resolve().relative_to(BASE_DIR.resolve())
        return True
    except Exception:
        return False


def _normalize_rel_path(rel_path: str) -> Path:
    rel_path = (rel_path or "").replace("\\", "/").strip().lstrip("/")
    path = (BASE_DIR / rel_path).resolve()
    if not _is_safe_path(path):
        raise ValueError("Path escapes project root")
    return path


def list_project_tree(max_depth: int = 3, max_items: int = 400) -> dict[str, Any]:
    root = BASE_DIR
    items = []
    count = 0

    for current_root, dirs, files in os.walk(root):
        current = Path(current_root)
        rel = current.relative_to(root)
        depth = len(rel.parts)

        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        if depth > max_depth:
            dirs[:] = []
            continue

        for d in sorted(dirs):
            items.append({"type": "dir", "path": str((rel / d).as_posix())})
            count += 1
            if count >= max_items:
                return {"ok": True, "root": str(root), "items": items, "count": len(items)}

        for f in sorted(files):
            items.append({"type": "file", "path": str((rel / f).as_posix())})
            count += 1
            if count >= max_items:
                return {"ok": True, "root": str(root), "items": items, "count": len(items)}

    return {"ok": True, "root": str(root), "items": items, "count": len(items)}


def read_project_file(rel_path: str, max_chars: int = 12000) -> dict[str, Any]:
    try:
        path = _normalize_rel_path(rel_path)
    except Exception as e:
        return {"ok": False, "error": str(e), "path": rel_path}

    if not path.exists() or not path.is_file():
        return {"ok": False, "error": "File not found", "path": rel_path}

    if path.suffix.lower() not in TEXT_EXTS:
        return {"ok": False, "error": f"Unsupported file type: {path.suffix}", "path": rel_path}

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        return {
            "ok": True,
            "path": str(path.relative_to(BASE_DIR).as_posix()),
            "chars": len(content[:max_chars]),
            "content": content[:max_chars],
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "path": rel_path}


def write_project_file(rel_path: str, content: str) -> dict[str, Any]:
    try:
        path = _normalize_rel_path(rel_path)
    except Exception as e:
        return {"ok": False, "error": str(e), "path": rel_path}

    if path.suffix.lower() not in TEXT_EXTS:
        return {"ok": False, "error": f"Unsupported file type: {path.suffix}", "path": rel_path}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")
        return {
            "ok": True,
            "path": str(path.relative_to(BASE_DIR).as_posix()),
            "chars_written": len(content or ""),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "path": rel_path}


def search_project(query: str, max_hits: int = 50) -> dict[str, Any]:
    q = (query or "").strip().lower()
    if not q:
        return {"ok": False, "error": "Empty query", "query": query}

    hits = []
    for current_root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            path = Path(current_root) / f
            if path.suffix.lower() not in TEXT_EXTS:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            low = text.lower()
            if q in low:
                idx = low.find(q)
                snippet = text[max(0, idx - 120): idx + 220].replace("\n", " ")
                hits.append({
                    "path": str(path.relative_to(BASE_DIR).as_posix()),
                    "snippet": snippet.strip(),
                })
                if len(hits) >= max_hits:
                    return {"ok": True, "query": query, "hits": hits, "count": len(hits)}

    return {"ok": True, "query": query, "hits": hits, "count": len(hits)}
