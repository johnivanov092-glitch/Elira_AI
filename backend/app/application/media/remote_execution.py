"""Remote OCR orchestration (R5C) — bind → egress → verify → derived ResourceRef.

Ties the run-bound source resource to the remote OCR worker and turns the result
into a NEW, run-bound resource that the existing ``resource_materialize`` /
``resource_publish`` tools consume unchanged. It adds no executor, provider,
registry, store or DB — only this orchestration and the transport client.

Order of operations (fail-closed at each step):
1. local input cap vs ``record.size`` — an oversize is refused with ZERO bytes
   read and ZERO HTTP;
2. config validation — an unavailable/unsafe worker is refused BEFORE any read;
3. ``GET /v1/capabilities`` first; operations are intersected with a local
   allowlist so the worker can never introduce a new executable operation;
4. effective cap = min(local cap, worker-advertised) re-checked before any read;
5. read the bound bytes; re-assert their digest matches the record;
6. exactly one ``POST /v1/jobs/ocr`` (retry=0);
7. strictly validate the response and re-verify ``sha256(text) == text_sha256``;
8. only then register the UTF-8 text as a new resource (owner inherited),
   atomically ``add_bound`` it, and on a bind failure ``discard`` it;
9. return ONLY a bounded projection — never the OCR text, bytes, or any host/URL/
   token/path/server message.

``RemoteJobResult`` is PRIVATE to this module and never crosses the tool boundary.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
from pathlib import Path
from typing import Any

from app.application.media import remote_worker_client as rwc
from app.application.media import resource_store, run_binding

logger = logging.getLogger(__name__)

_ALLOWED_OPERATIONS = frozenset({"ocr"})
_DERIVED_CONTENT_TYPE = "text/plain; charset=utf-8"
_DERIVED_SUFFIX = ".ocr.txt"

_ERROR_TEXT = {
    "remote_worker_unavailable": "the remote OCR worker is not configured or unavailable",
    "remote_operation_unsupported": "the remote worker does not offer this operation",
    "remote_input_too_large": "the resource is too large for remote processing",
    "remote_worker_failed": "the remote OCR worker could not process the resource",
    "remote_invalid_response": "the remote worker returned an invalid response",
    "remote_verify_failed": "the remote result failed integrity verification",
    "remote_bind_failed": "the remote result could not be attached to this run",
    "resource_blob_missing": "the resource bytes are no longer available",
}


@dataclasses.dataclass(frozen=True)
class RemoteJobResult:
    """PRIVATE — the full remote result including the OCR text. It is consumed
    here (registered as a resource) and never returned across the tool boundary;
    the public projection is built from the registered ``ResourceRecord``."""

    text: str
    pages: int
    confidence: float
    processing_ms: int


def _refusal(code: str) -> dict[str, Any]:
    """A stable execution-shaped refusal that never echoes a path/host/message."""
    return {"ok": False, "error": code, "text": f"ERROR: {_ERROR_TEXT[code]}"}


def _derived_name(original: str) -> str:
    """A safe metadata name for the derived text (the store re-sanitizes it)."""
    stem = Path(str(original or "")).stem.strip()
    return f"{stem or 'resource'}{_DERIVED_SUFFIX}"


def run_remote_ocr(*, record: resource_store.ResourceRecord, run_id: str,
                   client: rwc.WorkerClient | None = None) -> dict[str, Any]:
    """Process a run-bound resource on the remote OCR worker.

    ``client`` is injectable for tests; in production it is built from env only
    after the local size gate, so an oversize never triggers config I/O either."""
    # 1. Local cap FIRST — oversize is refused with zero read and zero HTTP.
    local_cap = rwc.max_input_bytes()
    if record.size > local_cap:
        return _refusal("remote_input_too_large")

    # 2. Config validation (no I/O, no read) — fail closed before touching bytes.
    if client is None:
        try:
            client = rwc.WorkerClient(rwc.resolve_config())
        except rwc.RemoteUnavailable as exc:
            logger.warning("remote worker unavailable: %s", exc.reason)
            return _refusal("remote_worker_unavailable")

    # 3. Capabilities first, then the operation allowlist intersection.
    try:
        caps = client.capabilities()
    except rwc.RemoteUnavailable as exc:
        logger.warning("remote worker unavailable: %s", exc.reason)
        return _refusal("remote_worker_unavailable")
    except rwc.RemoteTransportError as exc:
        logger.warning("remote capabilities transport failure: %s", exc.reason)
        return _refusal("remote_worker_failed")
    except rwc.RemoteProtocolError as exc:
        logger.warning("remote capabilities invalid: %s", exc.reason)
        return _refusal("remote_invalid_response")

    if "ocr" not in (set(caps.operations) & _ALLOWED_OPERATIONS):
        return _refusal("remote_operation_unsupported")

    # 4. Effective cap re-check before any byte is read.
    effective_cap = min(local_cap, caps.max_upload_bytes)
    if record.size > effective_cap:
        return _refusal("remote_input_too_large")

    # 5. Read the bound bytes; re-assert the recorded digest of what we send.
    try:
        data = resource_store.read_bytes(record)
    except resource_store.ResourceError as exc:
        code = exc.reason if exc.reason in _ERROR_TEXT else "remote_worker_failed"
        return _refusal(code)
    except Exception:  # noqa: BLE001 — never surface a raw path/exception
        return _refusal("remote_worker_failed")
    if hashlib.sha256(data).hexdigest() != record.sha256:
        return _refusal("remote_verify_failed")

    # 6. Exactly one POST (retry=0).
    try:
        payload = client.run_ocr_job(
            filename=record.original_name,
            content_type=record.content_type,
            data=data,
            content_sha256=record.sha256,
        )
    except rwc.RemoteTransportError as exc:
        logger.warning("remote job transport failure: %s", exc.reason)
        return _refusal("remote_worker_failed")
    except rwc.RemoteProtocolError as exc:
        logger.warning("remote job invalid response: %s", exc.reason)
        return _refusal("remote_invalid_response")

    # 7. Re-verify the text digest locally — do NOT trust the worker's claim.
    if hashlib.sha256(payload.text.encode("utf-8")).hexdigest() != payload.text_sha256:
        return _refusal("remote_verify_failed")

    result = RemoteJobResult(
        text=payload.text, pages=payload.page_count,
        confidence=payload.confidence, processing_ms=payload.processing_ms,
    )

    # 8. Register the derived text ONLY after verification. owner inherited.
    try:
        derived = resource_store.register_resource(
            original_name=_derived_name(record.original_name),
            content_type=_DERIVED_CONTENT_TYPE,
            owner_session=record.owner_session,
            data=result.text.encode("utf-8"),
        )
    except Exception:  # noqa: BLE001 — never surface a raw path/exception
        return _refusal("remote_worker_failed")

    # Atomic union into the run binding. If it fails, the just-registered resource
    # is unreachable by any run, so discard its blob AND metadata.
    bound = False
    try:
        bound = run_binding.add_bound(run_id, derived.resource_id)
    except Exception:  # noqa: BLE001
        bound = False
    if not bound:
        resource_store.discard(derived)
        return _refusal("remote_bind_failed")

    # 9. Bounded projection ONLY — exactly this key set. No OCR text, no bytes, no
    #    host/URL/token/path, no server message, no internal HTTP response.
    return {
        "ok": True,
        "operation": "ocr",
        "resource": resource_store.resource_ref(derived),
        "pages": result.pages,
        "confidence": result.confidence,
        "chars": len(result.text),
    }
