from __future__ import annotations

from pathlib import Path
from typing import Any


BLOCKED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".cache",
    "target",
    ".idea",
    ".vs",
}

TEXT_EXTS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".md",
    ".txt",
    ".rs",
    ".go",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".sh",
    ".bat",
    ".sql",
    ".xml",
    ".csv",
    ".env",
    ".bas",
    ".vba",
    ".cls",
}

_project_path: str = ""


def _project_root() -> Path | None:
    return Path(_project_path) if _project_path else None


def _root_for(project_root: str | None) -> tuple[Path | None, str | None]:
    """Resolve which project to operate on.

    If `project_root` is given, use THAT path (validated) — this is how the
    code-agent IDE drawer scopes to its own coding project without touching the
    chat's open project. If omitted, fall back to the global open project (the
    chat's docs/analysis project). The two surfaces therefore stay independent.
    """
    if project_root:
        try:
            p = Path(project_root).expanduser().resolve()
        except (OSError, RuntimeError):
            return None, "Invalid project root"
        if not p.is_dir():
            return None, "Project path does not exist"
        return p, None
    root = _project_root()
    if root is None:
        return None, "Project is not open"
    return root, None


def _resolve_inside(base: Path, relative_path: str) -> tuple[Path | None, str | None]:
    try:
        base = base.resolve()
        full_path = (base / relative_path).resolve()
        full_path.relative_to(base)
    except (OSError, RuntimeError, ValueError):
        return None, "Path is outside project"
    return full_path, None


def _resolve_inside_project(relative_path: str) -> tuple[Path | None, str | None]:
    root = _project_root()
    if root is None:
        return None, "Project is not open"
    return _resolve_inside(root, relative_path)


def _is_blocked_relative(path: Path) -> bool:
    return any(part in BLOCKED_DIRS for part in path.parts)


def open_project(path: str) -> dict[str, Any]:
    global _project_path

    try:
        project_path = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}

    if not project_path.exists() or not project_path.is_dir():
        return {"ok": False, "error": f"Directory not found: {path}"}

    _project_path = str(project_path)
    return {"ok": True, "path": _project_path, "name": project_path.name}


def get_project_info() -> dict[str, Any]:
    root = _project_root()
    if root is None:
        return {"ok": False, "error": "Project is not open"}

    return {
        "ok": True,
        "path": _project_path,
        "name": root.name,
        "exists": root.exists(),
    }


def project_tree(max_depth: int = 3, max_items: int = 300, project_root: str | None = None) -> dict[str, Any]:
    root, error = _root_for(project_root)
    if error or root is None:
        return {"ok": False, "error": error or "Project is not open", "items": []}
    if not root.exists():
        return {"ok": False, "error": "Project path does not exist", "items": []}

    items: list[dict[str, Any]] = []
    safe_max_depth = max(0, int(max_depth))
    safe_max_items = max(0, int(max_items))

    def walk(dir_path: Path, depth: int) -> None:
        if depth > safe_max_depth or len(items) >= safe_max_items:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (OSError, PermissionError):
            return

        for entry in entries:
            if entry.name.startswith(".") and entry.name != ".env":
                continue
            if entry.name in BLOCKED_DIRS:
                continue

            try:
                rel_path = entry.relative_to(root)
            except ValueError:
                continue

            rel = str(rel_path).replace("\\", "/")
            if entry.is_dir():
                items.append({"path": rel, "type": "dir", "name": entry.name})
                walk(entry, depth + 1)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                items.append(
                    {
                        "path": rel,
                        "type": "file",
                        "name": entry.name,
                        "size": size,
                        "ext": entry.suffix.lower(),
                    }
                )

            if len(items) >= safe_max_items:
                return

    walk(root, 0)
    return {"ok": True, "items": items, "count": len(items), "root": str(root)}


def read_project_file(path: str, max_chars: int = 20_000, project_root: str | None = None) -> dict[str, Any]:
    base, root_error = _root_for(project_root)
    if root_error or base is None:
        return {"ok": False, "error": root_error or "Project is not open"}
    full_path, error = _resolve_inside(base, path)
    if error:
        return {"ok": False, "error": error}
    if full_path is None or not full_path.exists() or not full_path.is_file():
        return {"ok": False, "error": f"File not found: {path}"}

    try:
        limit = max(0, int(max_chars))
        content = full_path.read_text(encoding="utf-8", errors="replace")[:limit]
        return {"ok": True, "path": path, "content": content, "size": full_path.stat().st_size}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def search_in_project(query: str, max_results: int = 20, project_root: str | None = None) -> dict[str, Any]:
    root, error = _root_for(project_root)
    if error or root is None:
        return {"ok": False, "error": error or "Project is not open"}

    normalized_query = (query or "").lower()
    safe_max_results = max(0, int(max_results))
    results: list[dict[str, Any]] = []

    try:
        paths = root.rglob("*")
        for file_path in paths:
            if len(results) >= safe_max_results:
                break
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in TEXT_EXTS:
                continue

            try:
                rel_path = file_path.relative_to(root)
            except ValueError:
                continue
            if _is_blocked_relative(rel_path):
                continue

            rel = str(rel_path).replace("\\", "/")
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for line_no, line in enumerate(content.split("\n"), 1):
                if normalized_query in line.lower():
                    results.append({"path": rel, "line": line_no, "text": line.strip()[:200]})
                    if len(results) >= safe_max_results:
                        break
    except (OSError, PermissionError):
        pass

    return {"ok": True, "items": results, "count": len(results), "query": query}


def close_project() -> dict[str, bool]:
    global _project_path
    _project_path = ""
    return {"ok": True}
