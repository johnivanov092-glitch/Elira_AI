from __future__ import annotations

from pathlib import Path
from typing import Any
import json

BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
LIBRARY_DIR = DATA_DIR / "uploads"
META_FILE = DATA_DIR / "library_meta.json"

LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

TEXT_EXTS = {".txt", ".md", ".py", ".json", ".csv", ".yml", ".yaml", ".log", ".html", ".js", ".ts", ".css"}

def _read_meta() -> dict[str, Any]:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _write_meta(data: dict[str, Any]) -> None:
    META_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def list_library_files() -> dict[str, Any]:
    meta = _read_meta()
    files = []
    for path in sorted(LIBRARY_DIR.glob("*")):
        if path.is_file():
            item_meta = meta.get(path.name, {})
            files.append({
                "name": path.name,
                "size": path.stat().st_size,
                "active": bool(item_meta.get("active", False)),
                "path": str(path),
                "suffix": path.suffix.lower(),
            })
    return {"ok": True, "files": files, "count": len(files)}

def set_library_active(filename: str, active: bool) -> dict[str, Any]:
    meta = _read_meta()
    item = meta.get(filename, {})
    item["active"] = bool(active)
    meta[filename] = item
    _write_meta(meta)
    return {"ok": True, "filename": filename, "active": bool(active)}

def delete_library_file(filename: str) -> dict[str, Any]:
    path = LIBRARY_DIR / filename
    if path.exists() and path.is_file():
        path.unlink()
    meta = _read_meta()
    if filename in meta:
        del meta[filename]
        _write_meta(meta)
    return {"ok": True, "filename": filename}

def _safe_read_text(path: Path, max_chars: int = 5000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""

def build_library_context(max_files: int = 3, max_chars_per_file: int = 4000) -> dict[str, Any]:
    library = list_library_files()
    active_files = [f for f in library.get("files", []) if f.get("active")]
    context_parts = []
    used_files = []

    for item in active_files[:max_files]:
        path = Path(item["path"])
        suffix = str(item.get("suffix", "")).lower()
        if suffix not in TEXT_EXTS:
            continue
        content = _safe_read_text(path, max_chars=max_chars_per_file)
        if not content.strip():
            continue
        used_files.append(item["name"])
        context_parts.append(f"===== FILE: {item['name']} =====\n{content}")

    return {
        "ok": True,
        "used_files": used_files,
        "active_count": len(active_files),
        "context": "\n\n".join(context_parts),
    }
