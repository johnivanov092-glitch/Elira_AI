"""Compatibility facade for Ollama model listing."""
from __future__ import annotations

from app.infrastructure.llm.ollama_models import OLLAMA_TAGS_URL, get_models

__all__ = ["OLLAMA_TAGS_URL", "get_models"]
