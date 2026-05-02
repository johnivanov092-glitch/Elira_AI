# -*- coding: utf-8 -*-
"""Application-layer runtime for Ollama model listing (via HTTP tags API).

Fetches the list of locally available Ollama models via the tags endpoint.
The ``requests`` import lives inside the function so the module is
importable without a running Ollama instance.
"""
from __future__ import annotations

from typing import Any

OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"


# ── public API ────────────────────────────────────────────────────────────────

def get_models() -> dict[str, Any]:
    """Return the list of locally available Ollama models.

    Returns:
      ok: bool
      models: list[dict]  — name, model, size, modified_at, digest
      count: int
      error: str          — present only on failure
    """
    try:
        import requests
        resp = requests.get(OLLAMA_TAGS_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        models = [
            {
                "name": item.get("name", ""),
                "model": item.get("model", ""),
                "size": item.get("size", 0),
                "modified_at": item.get("modified_at", ""),
                "digest": item.get("digest", ""),
            }
            for item in data.get("models", [])
        ]
        return {"ok": True, "models": models, "count": len(models)}
    except Exception as e:
        return {"ok": False, "models": [], "count": 0, "error": str(e)}
