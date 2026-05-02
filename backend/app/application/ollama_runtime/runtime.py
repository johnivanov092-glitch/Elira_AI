# -*- coding: utf-8 -*-
"""Application-layer runtime for Ollama model listing.

Compatible with both ollama>=0.1 (dict responses) and >=0.3 (object
responses).  No HTTP at module level — the ``ollama`` import happens
inside the async function.
"""
from __future__ import annotations


# ── public API ────────────────────────────────────────────────────────────────

async def list_ollama_models() -> dict:
    """Return the list of locally available Ollama models.

    Returns:
      models: list[dict]  — each entry has ``name`` (str) and ``size`` (int)
      error: str          — present only on failure
    """
    try:
        import ollama
        resp = ollama.list()

        # New versions: resp.models (list of objects)
        # Old versions: resp["models"] (list of dicts)
        raw = []
        if hasattr(resp, "models"):
            raw = resp.models or []
        elif isinstance(resp, dict):
            raw = resp.get("models", [])

        models = []
        for m in raw:
            name = ""
            if hasattr(m, "name"):
                name = m.name or ""
            elif hasattr(m, "model"):
                name = m.model or ""
            elif isinstance(m, dict):
                name = m.get("name", "") or m.get("model", "")

            if name:
                size = 0
                if hasattr(m, "size"):
                    size = m.size or 0
                elif isinstance(m, dict):
                    size = m.get("size", 0)
                models.append({"name": str(name), "size": size})

        return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}
