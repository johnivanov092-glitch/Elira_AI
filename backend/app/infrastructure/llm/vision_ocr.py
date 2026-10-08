"""Vision provider client and OCR configuration; OCR execution belongs to skills."""
from __future__ import annotations

import base64
import logging
import mimetypes
import os
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

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


# ───────────────────────────── Vision (:8004) ─────────────────────────────

@dataclass(frozen=True)
class VisionConfig:
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float
    max_tokens: int
    prompt: str


_DEFAULT_VISION_PROMPT = (
    "Опиши подробно, что изображено на картинке: объекты, люди, текст на "
    "изображении (процитируй его дословно), таблицы, схемы, диаграммы. "
    "Если это скриншот или документ — передай его содержимое. Ответь по-русски."
)


def vision_config() -> VisionConfig:
    return VisionConfig(
        base_url=_env_value("VISION_BASE_URL", "http://192.168.88.15:8004/v1").rstrip("/"),
        model=_env_value("VISION_MODEL", "vision-model").strip() or "vision-model",
        api_key=_env_value("VISION_API_KEY", "local").strip() or "local",
        timeout_seconds=_env_float("VISION_TIMEOUT_SECONDS", 180.0),
        max_tokens=_env_int("VISION_MAX_TOKENS", 1024),
        prompt=_env_value("VISION_PROMPT", _DEFAULT_VISION_PROMPT).strip() or _DEFAULT_VISION_PROMPT,
    )


def _data_url(filename: str, contents: bytes) -> str:
    mime = mimetypes.guess_type(filename)[0] or "image/png"
    if not mime.startswith("image/"):
        mime = "image/png"
    b64 = base64.b64encode(contents).decode("ascii")
    return f"data:{mime};base64,{b64}"


def describe_image(
    filename: str,
    contents: bytes,
    *,
    prompt: str | None = None,
    timeout_seconds: float | None = None,
) -> str | None:
    """Describe an image via the vision model (:8004). Returns the text
    description, or ``None`` if the call fails. Callers
    decide what to do with ``None`` (e.g. leave preview empty)."""
    cfg = vision_config()
    if not contents:
        return None
    try:
        payload = {
            "model": cfg.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": _data_url(filename, contents)}},
                        {"type": "text", "text": prompt or cfg.prompt},
                    ],
                }
            ],
            "max_tokens": cfg.max_tokens,
            "temperature": 0.2,
            "stream": False,
        }
        response = requests.post(
            f"{cfg.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_seconds if timeout_seconds is not None else cfg.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("vision describe_image failed for %s: %s", filename, exc)
        return None

    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    logger.warning("vision describe_image: empty response for %s", filename)
    return None


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
