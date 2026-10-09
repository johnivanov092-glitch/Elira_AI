"""Vision provider client and OCR configuration; OCR execution belongs to skills."""
from __future__ import annotations
from elira_common.environment import _env_bool as _env_bool, _env_value as _env_value, _env_float as _env_float, _env_int as _env_int

import base64
import json
import logging
import mimetypes
import os
import time
from typing import Any
from dataclasses import dataclass

import requests
from urllib3.exceptions import ReadTimeoutError

logger = logging.getLogger(__name__)











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


@dataclass(frozen=True)
class VisionResult:
    text: str | None = None
    error: str | None = None
    finish_reason: str | None = None
    status_code: int | None = None
    elapsed_ms: int = 0


def describe_image_result(
    filename: str,
    contents: bytes,
    *,
    prompt: str | None = None,
    timeout_seconds: float | None = None,
    response_schema: dict[str, Any] | None = None,
) -> VisionResult:
    """Single provider request; preserve failure kind without leaking image/prompt data."""
    cfg = vision_config()
    started = time.monotonic()
    timeout = timeout_seconds if timeout_seconds is not None else cfg.timeout_seconds
    deadline = started + timeout
    status_code = None
    def result(*, text=None, error=None, finish_reason=None):
        elapsed = round((time.monotonic()-started)*1000)
        if error:
            logger.warning("vision request failed: code=%s http=%s finish=%s elapsed_ms=%s", error, status_code, finish_reason, elapsed)
        return VisionResult(text=text, error=error, finish_reason=finish_reason, status_code=status_code, elapsed_ms=elapsed)
    if not contents:
        return result(error="vision_empty_image")
    payload = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": _data_url(filename, contents)}},
            {"type": "text", "text": prompt or cfg.prompt},
        ]}],
        "max_tokens": cfg.max_tokens,
        "temperature": 0 if response_schema is not None else 0.2,
        "stream": False,
    }
    if response_schema is not None:
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "document_page_layout", "strict": True, "schema": response_schema,
        }}
    response = None
    try:
        response = requests.post(
            f"{cfg.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
            stream=True,
        )
        status_code = response.status_code
        response.raise_for_status()
        # Requests' read timeout is an inactivity limit. Check elapsed time as
        # bytes arrive so a slow, continuously streaming body cannot extend it.
        body = bytearray()
        for chunk in response.iter_content(chunk_size=1):
            if time.monotonic() >= deadline:
                return result(error="vision_timeout")
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                return result(error="vision_provider_response_invalid")
        if time.monotonic() >= deadline:
            return result(error="vision_timeout")
        data = json.loads(body)
    except requests.Timeout:
        return result(error="vision_timeout")
    except requests.ConnectionError as exc:
        if isinstance(exc.__context__, ReadTimeoutError) or any(isinstance(arg, ReadTimeoutError) for arg in exc.args):
            return result(error="vision_timeout")
        return result(error="vision_connection_error")
    except requests.HTTPError as exc:
        if exc.response is not None:
            status_code = exc.response.status_code
        return result(error="vision_http_error")
    except (ValueError, RecursionError):
        return result(error="vision_provider_response_invalid")
    except requests.RequestException:
        return result(error="vision_transport_error")
    finally:
        if response is not None:
            response.close()
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return result(error="vision_provider_response_invalid")
    choice = choices[0]
    finish = choice.get("finish_reason")
    if finish not in (None, "stop"):
        return result(error="vision_response_truncated" if finish == "length" else "vision_response_incomplete", finish_reason=str(finish))
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        return result(error="vision_empty_response", finish_reason=finish)
    return result(text=content.strip(), finish_reason=finish)


def describe_image(filename: str, contents: bytes, *, prompt: str | None = None,
                   timeout_seconds: float | None = None) -> str | None:
    """Compatibility text API for image descriptions; QA consumes the typed result."""
    return describe_image_result(filename, contents, prompt=prompt, timeout_seconds=timeout_seconds).text
