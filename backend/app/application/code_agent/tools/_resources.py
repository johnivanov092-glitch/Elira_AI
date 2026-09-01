"""Resource processing tools over durable resource identifiers.

One general tool over durable resources. It takes ONLY a resource_id, an
operation, and an execution_target enum — never a storage path. Resource IDs are
durable across runs; Workflow permission is the only product authorization layer.
The runtime resolves the id, selects an adapter, and returns a bounded result.
"""
from __future__ import annotations

from typing import Any

from app.application.code_agent.document_validation import (
    DOCUMENT_QA_ATTEMPT_LIMIT,
    clear_document_qa_failures,
    document_sha256,
    document_qa_attempts,
    normalize_expected_page_count,
    record_document_qa_failure,
    validate_document,
)

_ERROR_TEXT = {
    "unknown_operation": "operation must be inspect, extract_text or transcribe",
    "invalid_execution_target": "execution_target must be auto, local_gpu, local_cpu or server_cpu",
    "unsupported_arguments": "resource_process accepts only resource_id, operation and execution_target",
    "resource_not_found": "resource not found",
}


def _refusal(code: str, *, requested_target: str | None) -> dict[str, Any]:
    """Return one stable execution-shaped refusal without echoing invalid input."""
    out: dict[str, Any] = {
        "ok": False,
        "error": code,
        "requested_target": requested_target,
        "selected_target": None,
        "backend": None,
        "text": f"ERROR: {_ERROR_TEXT[code]}",
    }
    if requested_target == "auto":
        out["fallback_chain"] = []
    return out


def tool_resource_process(resource_id: str = "", operation: str = "",
                          execution_target: str = "auto", **extra: Any) -> dict[str, Any]:
    """Process a durable resource. Args: resource_id (opaque
    id, NOT a path), operation (inspect | extract_text | transcribe), and
    execution_target (auto | local_gpu | local_cpu | server_cpu). Read-only; one
    bounded result; stable ``error`` code with ok=False on any refusal."""
    from app.application.media import execution, processing, resource_store

    resource_id = str(resource_id or "").strip()
    operation = str(operation or "").strip().lower()
    target = str(execution_target or "auto").strip().lower()

    if operation not in processing.SUPPORTED_OPERATIONS:
        safe_target = target if execution.valid_execution_target(target) else None
        return _refusal("unknown_operation", requested_target=safe_target)
    if not execution.valid_execution_target(target):
        return _refusal("invalid_execution_target", requested_target=None)
    target = execution.normalize_execution_target(target)
    # Do not silently accept path/url/argv/backend-like model arguments. The
    # runtime owns every execution detail; extra keys fail closed without echoing
    # their names or values.
    if extra:
        return _refusal("unsupported_arguments", requested_target=target)
    record = resource_store.get_record(resource_id)
    if record is None:
        return _refusal("resource_not_found", requested_target=target)
    return processing.process_resource(record, operation, target)


# ── resource_remote_process (R5C) — send a durable resource to the env-owned
#    remote OCR worker and register the recognized text as a new ResourceRef. ────

_REMOTE_ERROR_TEXT = {
    "unsupported_arguments": "resource_remote_process accepts only resource_id and operation",
    "unsupported_operation": "operation must be ocr",
    "resource_not_found": "resource not found",
}


def _remote_refusal(code: str) -> dict[str, Any]:
    """A stable refusal that never echoes the model input or any absolute path."""
    return {"ok": False, "error": code, "text": f"ERROR: {_REMOTE_ERROR_TEXT[code]}"}


def tool_resource_remote_process(resource_id: str = "", operation: str = "ocr",
                                 **extra: Any) -> dict[str, Any]:
    """Process a durable resource on the configured remote OCR
    worker and attach the recognized text to this run as a NEW resource. Args:
    resource_id (opaque id, NOT a path) and operation (only "ocr"). Sends only the
    resource bytes (data egress → approval); returns a bounded projection with a
    new resource_ref — never the OCR text, a host/URL/token, or a storage path.
    The derived resource works with resource_materialize / resource_publish."""
    from app.application.code_agent.tools import get_current_run_id
    from app.application.media import remote_execution, resource_store

    run_id = get_current_run_id()
    resource_id = str(resource_id or "").strip()
    operation = str(operation or "").strip().lower()

    # Reject any unexpected argument without echoing its name/value — the schema
    # already forbids extras; this is the defense-in-depth at dispatch.
    if extra:
        return _remote_refusal("unsupported_arguments")
    if operation != "ocr":
        return _remote_refusal("unsupported_operation")
    record = resource_store.get_record(resource_id)
    if record is None:
        return _remote_refusal("resource_not_found")
    return remote_execution.run_remote_ocr(record=record, run_id=run_id)


# ── resource_materialize (R4A) — bridge a durable ResourceRef into the run's
#    project workspace so the existing file/run_bash tools can process it. ──────

from pathlib import Path as _Path                             # noqa: E402
from urllib.parse import quote as _url_quote                 # noqa: E402

_MAX_NAME_CHARS = 200
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
    | {f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "¹²³"}
)

_MATERIALIZE_ERROR_TEXT = {
    "unsupported_arguments": "resource_materialize accepts only resource_id and destination_name",
    "resource_not_found": "resource not found",
    "invalid_destination": "destination must be a valid relative or absolute filesystem path",
    "destination_exists": "a file already exists at that destination; choose another name",
    "integrity_mismatch": "the materialized copy did not match the resource; nothing was written",
    "materialize_failed": "could not materialize the resource",
}


def _materialize_refusal(code: str) -> dict[str, Any]:
    """A stable refusal that never echoes the model input or any absolute path."""
    return {"ok": False, "error": code, "text": f"ERROR: {_MATERIALIZE_ERROR_TEXT[code]}"}


def _safe_basename(name: str) -> str:
    """A safe single-component filename from a stored resource name (metadata)."""
    raw = str(name or "").replace("\\", "/").split("/")[-1]
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ch not in '\r\n\t"<>:|?*')
    cleaned = cleaned.strip().strip(".")
    cleaned = cleaned[:_MAX_NAME_CHARS]
    return cleaned if _safe_component(cleaned) else "file"


def _safe_component(value: str) -> bool:
    """Reject names Windows can reinterpret as devices, ADS, or aliases."""
    if not value or value in {".", ".."} or value[-1] in {" ", "."}:
        return False
    if any(ord(ch) < 32 or ch in _WINDOWS_FORBIDDEN for ch in value):
        return False
    device_stem = value.split(".", 1)[0].rstrip(" .").upper()
    return device_stem not in _WINDOWS_RESERVED


def _safe_dest(project_root: _Path, name: str) -> _Path | None:
    """Resolve a relative project path or an absolute full-machine path."""
    raw = str(name or "")
    if not raw or raw != raw.strip():
        return None
    try:
        candidate = _Path(raw)
        if not candidate.is_absolute():
            candidate = project_root.resolve() / candidate
        resolved = candidate.resolve()
    except Exception:  # noqa: BLE001
        return None
    if resolved == project_root.resolve():
        return None
    return resolved


def tool_resource_materialize(project_root: Any, resource_id: str = "",
                              destination_name: str = "", **extra: Any) -> dict[str, Any]:
    """Materialize a durable resource into the local filesystem so the existing
    file/run_bash tools can process it. Args: resource_id (opaque id, NOT a path)
    and optional destination_name (relative to the project or absolute; default =
    the resource's safe basename). Writes a NEW file and never overwrites."""
    from app.application.media import resource_store

    resource_id = str(resource_id or "").strip()
    if extra:
        return _materialize_refusal("unsupported_arguments")
    record = resource_store.get_record(resource_id)
    if record is None:
        return _materialize_refusal("resource_not_found")

    root = _Path(str(project_root)).resolve()
    raw_destination = str(destination_name or "")
    requested = raw_destination if raw_destination.strip() else _safe_basename(record.original_name)
    dest = _safe_dest(root, requested)
    if dest is None:
        return _materialize_refusal("invalid_destination")
    if dest.exists():
        return _materialize_refusal("destination_exists")
    try:
        display_path = dest.relative_to(root).as_posix()
    except ValueError:
        display_path = str(dest)
    try:
        resource_store.materialize(
            record,
            dest.parent,
            dest.name,
            workspace_root=dest.parent,
        )
    except resource_store.ResourceError as exc:
        code = exc.reason if exc.reason in _MATERIALIZE_ERROR_TEXT else "materialize_failed"
        return _materialize_refusal(code)
    except Exception:  # noqa: BLE001 — never surface a raw path/exception
        return _materialize_refusal("materialize_failed")

    return {
        "ok": True,
        "resource_id": resource_id,
        "project_path": display_path,
        "size": record.size,
        "sha256": record.sha256,
        # Surface it through the existing touched-files/artifact flow.
        "touched_path": display_path,
        "diff_action": "create",
        "text": f"Materialized attached resource into {display_path} ({record.size} bytes)",
    }


# ── resource_publish (R4B) — deliver a processed workspace file to the user as a
#    structured download artifact via the EXISTING /api/skills/download route. ──

_PUBLISH_ERROR_TEXT = {
    "unsupported_arguments": "resource_publish accepts only project_path, download_name and expected_page_count",
    "invalid_source": "project_path must be a valid relative or absolute filesystem path",
    "source_not_file": "project_path must be an existing regular file",
    "source_outside_workspace": "project_path could not be read",
    "invalid_download_name": "download_name must be a plain filename (no path, no '..')",
    "destination_exists": "a download with that name already exists; choose another name",
    "integrity_mismatch": "the published copy did not match the source; nothing was published",
    "resource_too_large": "the file is too large to publish",
    "invalid_expected_page_count": "expected_page_count must be an integer from 1 to 100",
    "document_validation_failed": "document QA failed; fix the reported issues before publishing",
    "document_validation_unverified": "document QA could not produce a complete external verdict",
    "document_validation_attempts_exhausted": "this artifact already failed QA twice in the current run",
    "bom_validation_required": "a successful bom_validate call is required before publishing this BOM artifact",
    "publish_failed": "could not publish the file",
}

def _document_qa_refusal(code: str, qa: dict[str, Any]) -> dict[str, Any]:
    refusal = _publish_refusal(code)
    issues = qa.get("issues") if isinstance(qa.get("issues"), list) else []
    issue_text = "; ".join(
        f"{str(item.get('code') or 'issue')}: {str(item.get('message') or '').strip()}"
        for item in issues
        if isinstance(item, dict)
    )[:1500]
    refusal.update({
        "document_qa": qa,
        "sha256": str(qa.get("sha256") or ""),
        "verifier": True,
        "evidence": str(qa.get("status") or "unverified"),
        "text": (
            f"{refusal['text']} Document QA: status={qa.get('status')}; "
            f"page_count={qa.get('page_count')}; expected_page_count={qa.get('expected_page_count')}; "
            f"attempt={qa.get('attempt', 0)}/{DOCUMENT_QA_ATTEMPT_LIMIT}; "
            f"issues={issue_text or 'none'}."
        ),
    })
    return refusal


def _publish_refusal(code: str) -> dict[str, Any]:
    """A stable refusal that never echoes the model input or any absolute path."""
    return {"ok": False, "error": code, "text": f"ERROR: {_PUBLISH_ERROR_TEXT[code]}"}


def _safe_download_name(name: str) -> str | None:
    """A single-component download filename: no directories, no absolute/drive/UNC,
    no ADS/reserved/trailing-dot-space/control/leading-or-trailing-whitespace.
    Returns None if invalid (never silently normalizes a dangerous name)."""
    raw = str(name or "")
    if not raw or raw != raw.strip():             # reject leading/trailing whitespace
        return None
    if "/" in raw or "\\" in raw:                 # must be a bare filename, not a path
        return None
    if len(raw) > _MAX_NAME_CHARS or not _safe_component(raw):
        return None
    return raw


def tool_resource_publish(
    project_root: Any,
    project_path: str = "",
    download_name: str = "",
    expected_page_count: int | None = None,
    run_id: str = "",
    _runtime_refuse_reason: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """Publish an already-produced local file to the user as a structured
    download artifact. Args: project_path (relative to the project or absolute)
    and optional download_name (a plain safe
    filename, no directories; default = the source's safe basename). Copies the
    file (streaming, integrity-verified, no-overwrite) into the server download dir
    and returns a download_url for the existing /api/skills/download route — a
    source path."""
    from app.application.media import resource_store
    from app.core.config import DATA_DIR, GENERATED_DIR

    if _runtime_refuse_reason:
        return _publish_refusal("bom_validation_required")
    if extra:
        return _publish_refusal("unsupported_arguments")
    try:
        expected_page_count = normalize_expected_page_count(expected_page_count)
    except ValueError:
        return _publish_refusal("invalid_expected_page_count")
    root = _Path(str(project_root)).resolve()
    src = _safe_dest(root, str(project_path or ""))
    if src is None:
        return _publish_refusal("invalid_source")
    if not src.is_file():                          # rejects dir / device / missing
        return _publish_refusal("source_not_file")

    requested = str(download_name or "")
    chosen = requested if requested.strip() else _safe_basename(src.name)
    name = _safe_download_name(chosen)
    if name is None:
        return _publish_refusal("invalid_download_name")

    document_qa: dict[str, Any] | None = None
    if src.suffix.lower() in {".docx", ".pdf"}:
        source_sha256 = document_sha256(src)
        previous_attempts = document_qa_attempts(
            str(run_id or ""),
            name,
            expected_page_count,
        )
        if previous_attempts >= DOCUMENT_QA_ATTEMPT_LIMIT:
            return _document_qa_refusal(
                "document_validation_attempts_exhausted",
                {
                    "status": "failed",
                    "sha256": source_sha256,
                    "format": src.suffix.lower().lstrip("."),
                    "renderer": "not_run",
                    "page_count": None,
                    "expected_page_count": expected_page_count,
                    "vision_status": "not_run",
                    "attempt": previous_attempts,
                    "target": name,
                    "issues": [{
                        "code": "qa_attempts_exhausted",
                        "message": "Документ с этим именем уже дважды не прошёл document QA в текущем запуске.",
                    }],
                },
            )
        document_qa = dict(validate_document(
            src,
            expected_page_count=expected_page_count,
        ))
        document_qa["target"] = name
        qa_status = str(document_qa.get("status") or "unverified")
        if qa_status != "passed":
            attempt = record_document_qa_failure(
                str(run_id or ""),
                name,
                expected_page_count,
            )
            document_qa["attempt"] = attempt
            code = (
                "document_validation_failed"
                if qa_status == "failed"
                else "document_validation_unverified"
            )
            return _document_qa_refusal(code, document_qa)
        clear_document_qa_failures(str(run_id or ""), name)

    try:
        size, sha256 = resource_store.publish_copy(
            workspace_root=src.parent,
            source=src,
            destination_root=DATA_DIR,
            dest_dir=GENERATED_DIR,
            final_name=name,
            expected_sha256=(
                str(document_qa.get("sha256") or "")
                if document_qa is not None
                else None
            ),
        )
    except resource_store.ResourceError as exc:
        code = exc.reason if exc.reason in _PUBLISH_ERROR_TEXT else "publish_failed"
        return _publish_refusal(code)
    except Exception:  # noqa: BLE001 — never surface a raw path/exception
        return _publish_refusal("publish_failed")

    try:
        display_path = src.relative_to(root).as_posix()
    except ValueError:
        display_path = str(src)
    # download_name is set ONLY here, AFTER a verified atomic publish.  This proves
    # byte delivery, not that an arbitrary .pdf/.docx suffix contains that format.
    result = {
        "ok": True,
        "text": f"Published {display_path} as downloadable file {name} ({size} bytes).",
        "project_path": display_path,
        "download_url": f"/api/skills/download/{_url_quote(name, safe='')}",
        "download_name": name,
        "size": size,
        "sha256": sha256,
    }
    if document_qa is not None:
        result.update({
            "document_qa": document_qa,
            "verifier": True,
            "evidence": "document_qa_passed",
        })
    return result
