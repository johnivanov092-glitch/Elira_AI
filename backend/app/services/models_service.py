"""Models service — compatibility shim.

All logic lives in ``app.application.ollama_models.runtime``.
Public API re-exported for all callers: api/routes that list Ollama models.
"""
from __future__ import annotations

from app.application.ollama_models.runtime import OLLAMA_TAGS_URL, get_models

__all__ = ["OLLAMA_TAGS_URL", "get_models"]
