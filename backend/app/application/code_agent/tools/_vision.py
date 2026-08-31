from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.code_agent.tools._sandbox import _resolve_safe


# ─── vision / OCR on project files ────────────────────────────────────────────
#
# These let the agent autonomously "see" an image or extract text from a scanned
# document by wrapping the server vision (:8004) and OCR (:8002) clients. A tool
# call always attempts the configured service; transport failures are returned to
# the model without terminating the run.


def tool_read_image(
    project_root: Path,
    *,
    path: str = "",
    resource_id: str = "",
    prompt: str = "",
) -> dict[str, Any]:
    path = str(path or "").strip()
    resource_id = str(resource_id or "").strip()
    if bool(path) == bool(resource_id):
        return {
            "ok": False,
            "error": "path_or_resource_required",
            "text": "ERROR: provide exactly one of path or resource_id",
        }

    result_resource_id = ""
    if resource_id:
        from app.application.media import resource_store

        record = resource_store.get_record(resource_id)
        if record is None:
            return {"ok": False, "error": "resource_not_found",
                    "text": "ERROR: resource not found"}
        if record.kind != "image":
            return {"ok": False, "error": "unsupported_for_kind",
                    "text": "ERROR: read_image supports only image resources"}
        try:
            contents = resource_store.read_bytes(record)
        except Exception:  # noqa: BLE001 - keep resource internals out of tool output
            return {"ok": False, "error": "resource_read_failed",
                    "text": "ERROR: failed to read attached image"}
        image_name = record.original_name
        result_resource_id = record.resource_id
    else:
        target = _resolve_safe(project_root, path)
        if not target.is_file():
            return {"text": f"ERROR: not a file or does not exist: {path}",
                    "ok": False, "error": "file_not_found"}
        try:
            contents = target.read_bytes()
        except OSError:
            return {"text": f"ERROR: failed to read image {path}",
                    "ok": False, "error": "read_failed"}
        image_name = target.name

    try:
        from app.infrastructure.llm.vision_ocr import describe_image, is_vision_enabled
    except Exception:  # pragma: no cover - import guard
        return {"text": "ERROR: vision support unavailable",
                "ok": False, "error": "vision_unavailable"}

    try:
        description = describe_image(image_name, contents, prompt=(prompt or None))
    except Exception:  # noqa: BLE001 - provider internals must not cross the tool boundary
        description = None
    if not description:
        return {"text": "ERROR: vision returned no description (service unreachable or empty response).",
                "ok": False, "error": "vision_empty"}
    out: dict[str, Any] = {
        "ok": True,
        "text": f"Image description for {image_name}:\n{description}",
    }
    if result_resource_id:
        out["resource_id"] = result_resource_id
    return out


def tool_ocr_file(
    project_root: Path,
    *,
    path: str,
    language: str = "",
) -> dict[str, Any]:
    try:
        from app.infrastructure.llm.vision_ocr import is_ocr_enabled, ocr_document
    except Exception as exc:  # pragma: no cover - import guard
        return {"text": f"ERROR: OCR support unavailable: {exc}", "ok": False}

    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {path}", "ok": False}

    try:
        contents = target.read_bytes()
    except OSError as exc:
        return {"text": f"ERROR: failed to read file {path}: {exc}", "ok": False}

    text = ocr_document(target.name, contents, language=(language or None))
    if not text:
        return {"text": f"ERROR: OCR found no text in {path} (service unreachable or no recognizable text).", "ok": False}
    return {"ok": True, "text": f"OCR text from {path}:\n{text}"}
