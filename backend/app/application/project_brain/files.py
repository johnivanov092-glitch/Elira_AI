from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.application.project_brain.state import (
    ATTACHMENT_INDEX,
    EXCLUDED_PARTS,
    MAX_AGENT_FILE_BYTES,
    MAX_READ_BYTES,
    PROJECT_ROOT,
    TEXT_NAMES,
    TEXT_SUFFIXES,
)


def is_allowed(path: Path) -> bool:
    return not bool(set(path.parts) & EXCLUDED_PARTS)


def normalize_relative_path(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise HTTPException(status_code=400, detail="Path is required")

    normalized = raw_path.replace("\\", "/").strip().lstrip("/")
    rel = Path(normalized)

    if rel.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute paths are not allowed")
    if ".." in rel.parts:
        raise HTTPException(status_code=400, detail="Parent traversal is not allowed")
    return rel


def resolve_project_file(raw_path: str) -> tuple[Path, Path]:
    rel = normalize_relative_path(raw_path)
    full = (PROJECT_ROOT / rel).resolve()

    try:
        rel_from_root = full.relative_to(PROJECT_ROOT)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Path escapes project root") from exc

    if not is_allowed(rel_from_root):
        raise HTTPException(status_code=403, detail="Path is excluded")
    if not full.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not full.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    return full, rel_from_root


def looks_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.name in TEXT_NAMES


def read_text_file(full_path: Path) -> tuple[str, str, bytes]:
    raw = full_path.read_bytes()
    if b"\x00" in raw:
        raise HTTPException(status_code=415, detail="Binary files are not supported")
    try:
        content = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
        encoding = "utf-8/replace"
    return content, encoding, raw


def hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def snapshot_project_files() -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(PROJECT_ROOT)
        except Exception:
            continue
        if not is_allowed(rel):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append(
            {
                "path": str(rel).replace("\\", "/"),
                "name": path.name,
                "suffix": path.suffix.lower(),
                "size": stat.st_size,
            }
        )
    files.sort(key=lambda item: item["path"])
    return {
        "status": "ok",
        "project_root": str(PROJECT_ROOT),
        "files": files,
        "files_count": len(files),
    }


def read_project_file_payload(path: str) -> dict[str, Any]:
    full_path, rel_path = resolve_project_file(path)
    if not looks_text_file(full_path):
        raise HTTPException(status_code=415, detail="Only text-like source files are readable")
    size = full_path.stat().st_size
    if size > MAX_READ_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is too large to open ({size} bytes)",
        )
    content, encoding, raw = read_text_file(full_path)
    return {
        "status": "ok",
        "path": str(rel_path).replace("\\", "/"),
        "name": full_path.name,
        "suffix": full_path.suffix.lower(),
        "size": size,
        "encoding": encoding,
        "sha256": hash_bytes(raw),
        "content": content,
    }


def read_reference_context(
    paths: list[str],
    selected_path: str | None = None,
) -> list[dict[str, str]]:
    context_items: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in paths[:12]:
        if not path or path == selected_path or path in seen:
            continue
        seen.add(path)
        try:
            full_path, rel_path = resolve_project_file(path)
        except HTTPException:
            continue
        if not looks_text_file(full_path):
            continue
        try:
            size = full_path.stat().st_size
        except OSError:
            continue
        if size > MAX_AGENT_FILE_BYTES:
            continue
        try:
            text, _, _ = read_text_file(full_path)
        except HTTPException:
            continue
        context_items.append(
            {
                "path": str(rel_path).replace("\\", "/"),
                "content": text[:20_000],
            }
        )
    return context_items


def attach_project_file(path: str) -> dict[str, Any]:
    full_path, rel_path = resolve_project_file(path)
    if not looks_text_file(full_path):
        raise HTTPException(
            status_code=415,
            detail="Only text-like project files can be attached",
        )
    if full_path.stat().st_size > MAX_READ_BYTES:
        raise HTTPException(status_code=413, detail="Project file is too large")
    content, _, raw = read_text_file(full_path)
    project_path = str(rel_path).replace("\\", "/")
    item = {
        "id": uuid.uuid4().hex[:16],
        "name": project_path,
        "size": len(raw),
        "suffix": full_path.suffix.lower(),
        "path": str(full_path),
        "source": "project",
        "encoding": "utf-8",
        "text": content[:40_000],
        "text_available": True,
        "sha256": hash_bytes(raw),
        "created_at": time.time(),
        "project_path": project_path,
    }
    ATTACHMENT_INDEX[item["id"]] = item
    return item

