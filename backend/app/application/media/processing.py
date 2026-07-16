"""Deferred resource processing (R1) — inspect / extract_text / transcribe.

Runs ONLY on an explicit ``resource_process`` tool call, never at upload time.
Reuses the existing runtimes (no new extractor, no new STT client): documents go
through ``file_extract.extract_file``; audio/video containers go through the
existing STT runtime ``voice.runtime.transcribe``. Results are bounded; every
failure is a stable machine-readable ``error`` with ``ok=False`` (a processing
error can never be silently reported as success).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.file_extract.runtime import TEXT_EXTS, _AUDIO_EXTS
from app.application.media import resource_store

INSPECT = "inspect"
EXTRACT_TEXT = "extract_text"
TRANSCRIBE = "transcribe"
SUPPORTED_OPERATIONS = (INSPECT, EXTRACT_TEXT, TRANSCRIBE)

_MAX_RESULT_CHARS = 20000
_STT_TIMEOUT_SECONDS = 3600

# Extensions extract_text may hand to file_extract. Audio/video containers are
# excluded on purpose: extract_file would route an audio extension into STT, so a
# document-only op must never reach them (that is what transcribe is for).
_EXTRACT_TEXT_EXTS = (
    {".pdf", ".docx", ".doc", ".pptx", ".xls", ".xlsx", ".xlsm", ".zip"} | set(TEXT_EXTS)
)


def _err(operation: str, resource_id: str, code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "operation": operation, "resource_id": resource_id,
            "error": code, "text": f"ERROR: {message}"}


def _looks_like_extract_error(text: str) -> bool:
    """file_extract signals failure in-band as a bracketed Russian string with
    ok=True — detect it (same heuristic as the chat attach route) so a wrapper
    never reports a parse failure as success."""
    stripped = str(text or "").strip()
    if not stripped.startswith("["):
        return False
    low = stripped.lower()
    return ("ошибка" in low or "не установлен" in low or "не удалось" in low
            or "не поддерживается" in low)


def _inspect(record: resource_store.ResourceRecord) -> dict[str, Any]:
    ref = resource_store.resource_ref(record)
    summary = (f"resource {ref['resource_id']}: name={ref['name']} kind={ref['kind']} "
               f"type={ref['content_type']} size={ref['size']} sha256={record.sha256}")
    return {"ok": True, "operation": INSPECT, "resource_id": record.resource_id,
            "kind": record.kind, "name": record.original_name,
            "content_type": record.content_type, "size": record.size,
            "sha256": record.sha256, "created_at": record.created_at,
            "text": summary}


def _extract_text(record: resource_store.ResourceRecord) -> dict[str, Any]:
    from app.application.file_extract.runtime import extract_file

    ext = Path(record.original_name).suffix.lower()
    if ext in _AUDIO_EXTS or ext not in _EXTRACT_TEXT_EXTS:
        return _err(EXTRACT_TEXT, record.resource_id, "unsupported_for_kind",
                    f"extract_text does not support {record.kind or 'this'} resources "
                    f"(ext={ext or 'none'}); use transcribe for audio/video")
    try:
        data = resource_store.read_bytes(record)
        result = extract_file(record.original_name, data)
    except Exception:  # noqa: BLE001 — never surface extractor internals/paths
        return _err(EXTRACT_TEXT, record.resource_id, "extraction_failed",
                    "text extraction failed")
    if not isinstance(result, dict) or result.get("ok") is False:
        return _err(EXTRACT_TEXT, record.resource_id, "extraction_failed",
                    "text extraction failed")
    text = str(result.get("text", "") or "")
    if _looks_like_extract_error(text):
        return _err(EXTRACT_TEXT, record.resource_id, "extraction_failed",
                    "text extraction failed")
    text = text[:_MAX_RESULT_CHARS]
    return {"ok": True, "operation": EXTRACT_TEXT, "resource_id": record.resource_id,
            "kind": record.kind, "chars": len(text), "text": text}


def _transcribe(record: resource_store.ResourceRecord) -> dict[str, Any]:
    from app.application.voice.runtime import transcribe

    ext = Path(record.original_name).suffix.lower()
    if ext not in _AUDIO_EXTS:
        return _err(TRANSCRIBE, record.resource_id, "unsupported_for_kind",
                    "transcribe supports voice/audio containers "
                    "(mp3, m4a, wav, ogg, opus, flac, aac, mp4, webm); "
                    f"ext={ext or 'none'} is not one of them")
    data = resource_store.read_bytes(record)
    try:
        # Reuse the existing remote STT runtime (unchanged) — R1 only moves the
        # call from upload-time to this explicit tool-time. The original filename
        # carries the container extension the STT service decodes.
        text = transcribe(
            data,
            filename=record.original_name,
            language=None,
            timeout=_STT_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 — never surface the raw STT error/payload
        return _err(TRANSCRIBE, record.resource_id, "transcription_failed",
                    "speech-to-text failed")
    text = str(text or "").strip()
    if not text:
        return _err(TRANSCRIBE, record.resource_id, "transcription_empty",
                    "speech-to-text returned no text")
    text = text[:_MAX_RESULT_CHARS]
    return {"ok": True, "operation": TRANSCRIBE, "resource_id": record.resource_id,
            "kind": record.kind, "chars": len(text), "text": text}


def process_resource(record: resource_store.ResourceRecord, operation: str) -> dict[str, Any]:
    """Dispatch one bounded read-only operation on an already-authorized resource.
    The caller MUST have verified the run binding first."""
    op = str(operation or "").strip().lower()
    if op == INSPECT:
        return _inspect(record)
    if op == EXTRACT_TEXT:
        return _extract_text(record)
    if op == TRANSCRIBE:
        return _transcribe(record)
    return _err(op or "unknown", record.resource_id, "unknown_operation",
                "unsupported resource operation")
