from __future__ import annotations

import html
import re
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from app.application.project_brain.files import hash_bytes
from app.application.project_brain.state import (
    ATTACHMENT_INDEX,
    TEXT_SUFFIXES,
    TMP_UPLOAD_TTL_SECONDS,
    UPLOAD_ROOT,
)


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name or "attachment")
    return cleaned[:120] or "attachment"


def extract_text_from_docx(data: bytes) -> str:
    try:
        with tempfile.TemporaryDirectory(prefix="elira_docx_") as tmp:
            path = Path(tmp) / "file.docx"
            path.write_bytes(data)
            with zipfile.ZipFile(path, "r") as zf:
                xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<[^>]+>", "", xml)
        return html.unescape(xml)
    except Exception:
        return ""


def extract_text_from_pdf(data: bytes) -> str:
    text = data.decode("latin-1", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^\x20-\x7E\n\rА-Яа-яЁё]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_upload_text(filename: str, data: bytes) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES or suffix in {".pyw", ".log"}:
        try:
            return data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace"), "utf-8/replace"
    if suffix == ".docx":
        return extract_text_from_docx(data), "docx-text"
    if suffix == ".pdf":
        return extract_text_from_pdf(data), "pdf-text"
    return "", "binary"


def cleanup_stale_temp_uploads() -> None:
    now = time.time()
    stale_ids: list[str] = []
    for attachment_id, item in list(ATTACHMENT_INDEX.items()):
        created_at = float(item.get("created_at") or 0)
        path_str = item.get("path") or ""
        if not created_at or (now - created_at) <= TMP_UPLOAD_TTL_SECONDS:
            continue
        try:
            path = Path(path_str)
            if path.exists() and path.is_file():
                path.unlink()
        except Exception:
            pass
        stale_ids.append(attachment_id)
    for attachment_id in stale_ids:
        ATTACHMENT_INDEX.pop(attachment_id, None)

    for path in UPLOAD_ROOT.glob("*"):
        try:
            if path.is_file() and (now - path.stat().st_mtime) > TMP_UPLOAD_TTL_SECONDS:
                path.unlink()
        except Exception:
            pass


def store_attachment(filename: str, data: bytes, source: str = "upload") -> dict[str, Any]:
    cleanup_stale_temp_uploads()
    attachment_id = uuid.uuid4().hex[:16]
    safe_name = safe_filename(filename)
    disk_path = UPLOAD_ROOT / f"{attachment_id}_{safe_name}"
    disk_path.write_bytes(data)
    text, encoding = extract_upload_text(filename, data)
    item = {
        "id": attachment_id,
        "name": filename,
        "safe_name": safe_name,
        "size": len(data),
        "suffix": Path(filename).suffix.lower(),
        "path": str(disk_path),
        "source": source,
        "encoding": encoding,
        "text": text[:40_000],
        "text_available": bool(text.strip()),
        "sha256": hash_bytes(data),
        "created_at": time.time(),
    }
    ATTACHMENT_INDEX[attachment_id] = item
    return item


def attachment_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["name"],
        "size": item["size"],
        "suffix": item["suffix"],
        "source": item["source"],
        "text_available": item["text_available"],
        "preview": item.get("text", "")[:1200],
    }

