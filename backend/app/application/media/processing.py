"""Deferred resource processing (R1 + R2) — inspect / extract_text / transcribe.

Runs ONLY on an explicit ``resource_process`` tool call, never at upload time.
Reuses the existing runtimes (no new extractor, no new STT client): documents go
through ``file_extract.extract_file``; audio/video containers are transcribed by a
workload adapter chosen for the requested execution target (R2). Results are
bounded; every failure is a stable machine-readable ``error`` with ``ok=False``
(a processing error can never be silently reported as success).

R2 adds ``execution_target`` (auto | local_gpu | local_cpu | server_gpu). inspect
and extract_text run only on local_cpu; transcribe is routed by the selector in
``execution`` — the general compute-target framework, of which transcription is
the first workload adapter.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.application.file_extract.runtime import TEXT_EXTS, _AUDIO_EXTS
from app.application.media import execution, resource_store

logger = logging.getLogger(__name__)

INSPECT = "inspect"
EXTRACT_TEXT = "extract_text"
TRANSCRIBE = "transcribe"
SUPPORTED_OPERATIONS = (INSPECT, EXTRACT_TEXT, TRANSCRIBE)

_MAX_RESULT_CHARS = 20000
_LOCAL_CPU = execution.ExecutionTarget.LOCAL_CPU.value
_SAFE_ADAPTER_ERRORS = frozenset({
    "transcription_failed",
    "transcription_empty",
    "local_gpu_unavailable",
    "server_gpu_unavailable",
    "local_cpu_unavailable",
})

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


def _execution_result(result: dict[str, Any], *, requested_target: str,
                      selected_target: str | None, backend: str | None,
                      fallback_chain: tuple[dict[str, str], ...] = ()) -> dict[str, Any]:
    """Attach the public execution decision without exposing runtime details."""
    result["requested_target"] = requested_target
    result["selected_target"] = selected_target
    result["backend"] = backend
    # Keep the R1/R2 compatibility field while callers migrate to the explicit
    # requested/selected axes.
    result["execution_target"] = selected_target or requested_target
    if requested_target == execution.ExecutionTarget.AUTO.value:
        result["fallback_chain"] = [dict(item) for item in fallback_chain]
    return result


def _selection_error(operation: str, resource_id: str, requested_target: str,
                     sel: "execution.Selection") -> dict[str, Any]:
    out = _err(operation, resource_id, sel.error or "routing_error", sel.message or "routing failed")
    if sel.available_targets:
        out["available_targets"] = list(sel.available_targets)
    return _execution_result(
        out,
        requested_target=requested_target,
        selected_target=None,
        backend=None,
        fallback_chain=sel.fallback_chain,
    )


def _inspect(record: resource_store.ResourceRecord) -> dict[str, Any]:
    ref = resource_store.resource_ref(record)
    summary = (f"resource {ref['resource_id']}: name={ref['name']} kind={ref['kind']} "
               f"type={ref['content_type']} size={ref['size']} sha256={record.sha256}")
    return {"ok": True, "operation": INSPECT, "resource_id": record.resource_id,
            "kind": record.kind, "name": record.original_name,
            "content_type": record.content_type, "size": record.size,
            "sha256": record.sha256, "created_at": record.created_at,
            "execution_target": _LOCAL_CPU, "text": summary}


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
            "kind": record.kind, "execution_target": _LOCAL_CPU,
            "chars": len(text), "text": text}


def _transcribe(record: resource_store.ResourceRecord, execution_target: str,
                 adapters: "execution.AdapterSet | None") -> dict[str, Any]:
    # Format gate first — a non-audio resource is unsupported regardless of target.
    ext = Path(record.original_name).suffix.lower()
    if ext not in _AUDIO_EXTS:
        return _execution_result(
            _err(TRANSCRIBE, record.resource_id, "unsupported_for_kind",
                 "transcribe supports voice/audio containers "
                 "(mp3, m4a, wav, ogg, opus, flac, aac, mp4, webm); "
                 f"ext={ext or 'none'} is not one of them"),
            requested_target=execution_target,
            selected_target=None,
            backend=None,
        )
    # Route to a workload adapter for the requested execution target (fallback
    # only for auto: local_gpu → server_gpu → local_cpu).
    sel = execution.select(TRANSCRIBE, execution_target, adapters)
    if sel.error is not None or sel.adapter is None:
        return _selection_error(TRANSCRIBE, record.resource_id, execution_target, sel)

    def _run(adapter: "execution.WorkloadAdapter") -> dict[str, Any]:
        try:
            result = adapter.run(record)
        except Exception as exc:  # noqa: BLE001 — adapter internals never cross the boundary
            logger.warning(
                "resource_execution_failed target=%s resource_id=%s error=%s exception_class=%s",
                adapter.target, record.resource_id, "execution_failed", type(exc).__name__,
            )
            return _err(TRANSCRIBE, record.resource_id, "execution_failed",
                        "resource execution failed")
        if not isinstance(result, dict) or result.get("ok") not in (True, False):
            logger.warning(
                "resource_execution_failed target=%s resource_id=%s error=%s",
                adapter.target, record.resource_id, "invalid_adapter_result",
            )
            return _err(TRANSCRIBE, record.resource_id, "execution_failed",
                        "resource execution failed")
        return result

    if execution_target == execution.ExecutionTarget.AUTO.value:
        fallback_chain: list[dict[str, str]] = []
        last_failure = "all_execution_targets_failed"
        for candidate in sel.candidates:
            adapter = candidate.adapter
            if adapter is None:
                fallback_chain.append({
                    "target": candidate.target,
                    "reason": candidate.reason or f"{candidate.target}_unavailable",
                })
                continue
            if not execution.adapter_capability(adapter).available:
                fallback_chain.append({
                    "target": candidate.target,
                    "reason": f"{candidate.target}_unavailable",
                })
                continue
            result = _run(adapter)
            if result.get("ok") is True:
                return _execution_result(
                    result,
                    requested_target=execution_target,
                    selected_target=candidate.target,
                    backend=adapter.backend,
                    fallback_chain=tuple(fallback_chain),
                )
            raw_reason = str(result.get("error") or "")
            reason = raw_reason if raw_reason in _SAFE_ADAPTER_ERRORS else "execution_failed"
            last_failure = reason
            fallback_chain.append({
                "target": candidate.target,
                "backend": adapter.backend,
                "reason": reason,
            })
        return _execution_result(
            _err(TRANSCRIBE, record.resource_id, last_failure,
                 "all available transcription targets failed"),
            requested_target=execution_target,
            selected_target=None,
            backend=None,
            fallback_chain=tuple(fallback_chain),
        )

    result = _run(sel.adapter)
    return _execution_result(
        result,
        requested_target=execution_target,
        selected_target=sel.target,
        backend=sel.adapter.backend,
        fallback_chain=sel.fallback_chain,
    )


def process_resource(record: resource_store.ResourceRecord, operation: str,
                     execution_target: str = execution.ExecutionTarget.AUTO.value,
                     adapters: "execution.AdapterSet | None" = None) -> dict[str, Any]:
    """Dispatch one bounded read-only operation on an already-authorized resource,
    on the chosen execution target. The caller MUST have verified the run binding
    first. inspect/extract_text run only on local_cpu; transcribe is routed."""
    op = str(operation or "").strip().lower()
    target = str(execution_target or execution.ExecutionTarget.AUTO.value).strip().lower()

    if op in (INSPECT, EXTRACT_TEXT):
        sel = execution.select(op, target, adapters)
        if sel.error is not None:            # a GPU/server target for a local-only op
            return _selection_error(op, record.resource_id, target, sel)
        result = _inspect(record) if op == INSPECT else _extract_text(record)
        backend = "resource-inspect" if op == INSPECT else "file-extract"
        return _execution_result(
            result,
            requested_target=target,
            selected_target=_LOCAL_CPU,
            backend=backend,
        )
    if op == TRANSCRIBE:
        return _transcribe(record, target, adapters)
    return _err(op or "unknown", record.resource_id, "unknown_operation",
                "unsupported resource operation")
