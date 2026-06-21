from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException

from app.infrastructure.llm.openai_compatible import (
    chat_completion,
    local_llm_config,
)
from app.infrastructure.llm.local_models import get_models


def fetch_local_model_tags() -> dict[str, Any]:
    payload = get_models()
    return {"models": payload.get("models", [])}


def pick_model(requested_model: str | None, tags_payload: dict[str, Any]) -> str:
    names = [
        str(item.get("name") or item.get("model") or "").strip()
        for item in tags_payload.get("models") or []
        if isinstance(item, dict)
    ]
    if requested_model and requested_model.strip():
        requested = requested_model.strip()
        if not names or requested in names:
            return requested
        raise HTTPException(status_code=400, detail=f"Model is not available: {requested}")

    cfg = local_llm_config()
    if cfg.enabled:
        return cfg.model
    if names:
        return names[0]
    raise HTTPException(status_code=503, detail="No local LLM models are available")


def call_local_model_json(model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    response = chat_completion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        options={"temperature": 0.15},
    )
    content = (((response.get("message") or {}).get("content")) or "").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            return json.loads(match.group(0))
        raise HTTPException(status_code=502, detail="Model did not return valid JSON")


def local_model_status_payload(requested_model: str | None = None) -> dict[str, Any]:
    tags = fetch_local_model_tags()
    model_names = [
        str(item.get("name") or item.get("model") or "").strip()
        for item in tags.get("models") or []
        if isinstance(item, dict)
    ]
    default_model = pick_model(requested_model, tags) if model_names or local_llm_config().enabled else ""
    cfg = local_llm_config()
    return {
        "status": "ok" if cfg.enabled else "disabled",
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "models": model_names,
        "default_model": default_model,
    }
