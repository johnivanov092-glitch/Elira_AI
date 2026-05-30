"""Ollama HTTP client — raw urllib requests to the Ollama REST API."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from fastapi import HTTPException

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))


def _make_json_request(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urlrequest.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with urlrequest.urlopen(req, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Ollama HTTP error: {detail}") from exc
    except urlerror.URLError as exc:
        raise HTTPException(status_code=503, detail=f"Ollama is unavailable at {OLLAMA_BASE_URL}") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Ollama request timed out") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Invalid JSON returned by Ollama") from exc


def fetch_ollama_tags() -> dict[str, Any]:
    return _make_json_request(f"{OLLAMA_BASE_URL}/api/tags")


def pick_model(requested_model: str | None, tags_payload: dict[str, Any]) -> str:
    models = tags_payload.get("models") or []
    names = [item.get("name") for item in models if item.get("name")]
    if requested_model and requested_model.strip():
        return requested_model.strip()
    if DEFAULT_OLLAMA_MODEL:
        return DEFAULT_OLLAMA_MODEL
    if names:
        return names[0]
    raise HTTPException(status_code=400, detail="No Ollama models found. Pull a model first.")


def call_ollama_json(model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    import re
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": 0.15},
    }
    result = _make_json_request(f"{OLLAMA_BASE_URL}/api/chat", payload)
    content = (((result.get("message") or {}).get("content")) or "").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            return json.loads(match.group(0))
        raise HTTPException(status_code=502, detail="Model did not return valid JSON")
