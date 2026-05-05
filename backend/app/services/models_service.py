"""Compatibility facade for Ollama model listing."""
from __future__ import annotations

from app.application.ollama_models.runtime import OLLAMA_TAGS_URL, get_models

__all__ = ["OLLAMA_TAGS_URL", "get_models"]
