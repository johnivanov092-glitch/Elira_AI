"""OCR provider configuration; image interpretation is owned by the vision skill."""
from __future__ import annotations
from elira_common.environment import _env_bool as _env_bool, _env_value as _env_value, _env_float as _env_float, _env_int as _env_int
from dataclasses import dataclass
import os











# ─────────────────────────────── OCR (:8002) ───────────────────────────────

@dataclass(frozen=True)
class OcrConfig:
    url: str
    language: str
    pdf_fallback: bool
    timeout_seconds: float


def ocr_config() -> OcrConfig:
    return OcrConfig(
        url=_env_value("OCR_URL", "http://192.168.88.15:8002/ocr").rstrip("/"),
        language=_env_value("OCR_LANGUAGE", "auto").strip() or "auto",
        pdf_fallback=_env_bool("OCR_PDF_FALLBACK", True),
        timeout_seconds=_env_float("OCR_TIMEOUT_SECONDS", 300.0),
    )
