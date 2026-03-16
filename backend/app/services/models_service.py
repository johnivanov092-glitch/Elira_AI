from __future__ import annotations

from typing import Any

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
