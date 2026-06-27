from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.code_agent.tools._sandbox import _resolve_safe


# ─── vision / OCR on project files ────────────────────────────────────────────
#
# These let the agent autonomously "see" an image or extract text from a scanned
# document already present in the project, by wrapping the server vision (:8004)
# and OCR (:8002) clients. Both clients are env-gated (VISION_ENABLED /
# OCR_ENABLED, default OFF) and fail-closed (return None when disabled or on
# failure); we surface that as a clear, non-fatal ERROR string so the agent can
# react rather than crash the run.


def tool_read_image(
    project_root: Path,
    *,
    path: str,
    prompt: str = "",
) -> dict[str, Any]:
    try:
        from app.infrastructure.llm.vision_ocr import describe_image, is_vision_enabled
    except Exception as exc:  # pragma: no cover - import guard
        return {"text": f"ERROR: vision support unavailable: {exc}"}

    if not is_vision_enabled():
        return {"text": "ERROR: vision is disabled (set VISION_ENABLED=1 on the server to enable read_image)."}

    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {path}"}

    try:
        contents = target.read_bytes()
    except OSError as exc:
        return {"text": f"ERROR: failed to read image {path}: {exc}"}

    description = describe_image(target.name, contents, prompt=(prompt or None))
    if not description:
        return {"text": f"ERROR: vision returned no description for {path} (service unreachable or empty response)."}
    return {"text": f"Image description for {path}:\n{description}"}


def tool_ocr_file(
    project_root: Path,
    *,
    path: str,
    language: str = "",
) -> dict[str, Any]:
    try:
        from app.infrastructure.llm.vision_ocr import is_ocr_enabled, ocr_document
    except Exception as exc:  # pragma: no cover - import guard
        return {"text": f"ERROR: OCR support unavailable: {exc}"}

    if not is_ocr_enabled():
        return {"text": "ERROR: OCR is disabled (set OCR_ENABLED=1 on the server to enable ocr_file)."}

    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {path}"}

    try:
        contents = target.read_bytes()
    except OSError as exc:
        return {"text": f"ERROR: failed to read file {path}: {exc}"}

    text = ocr_document(target.name, contents, language=(language or None))
    if not text:
        return {"text": f"ERROR: OCR found no text in {path} (service unreachable or no recognizable text)."}
    return {"text": f"OCR text from {path}:\n{text}"}
