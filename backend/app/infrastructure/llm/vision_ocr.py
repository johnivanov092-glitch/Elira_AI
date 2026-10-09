"""OCR provider configuration; image interpretation is owned by the vision skill."""
from __future__ import annotations
from dataclasses import dataclass
import os

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_value(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return default if raw is None else raw


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


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
