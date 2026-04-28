from __future__ import annotations

from typing import Any

import ollama
import requests


OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"


def get_models() -> dict[str, Any]:
    try:
        resp = requests.get(OLLAMA_TAGS_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        models = []
        for item in data.get("models", []):
            models.append(
                {
                    "name": item.get("name", ""),
                    "model": item.get("model", ""),
                    "size": item.get("size", 0),
                    "modified_at": item.get("modified_at", ""),
                    "digest": item.get("digest", ""),
                }
            )
        return {"ok": True, "models": models, "count": len(models)}
    except Exception as e:
        return {"ok": False, "models": [], "count": 0, "error": str(e)}


async def list_ollama_models() -> dict[str, Any]:
    try:
        resp = ollama.list()

        raw = []
        if hasattr(resp, "models"):
            raw = resp.models or []
        elif isinstance(resp, dict):
            raw = resp.get("models", [])

        models = []
        for item in raw:
            name = ""
            if hasattr(item, "name"):
                name = item.name or ""
            elif hasattr(item, "model"):
                name = item.model or ""
            elif isinstance(item, dict):
                name = item.get("name", "") or item.get("model", "")

            if name:
                size = 0
                if hasattr(item, "size"):
                    size = item.size or 0
                elif isinstance(item, dict):
                    size = item.get("size", 0)
                models.append({"name": str(name), "size": size})

        return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}
