"""Compatibility facade for deprecated Ollama model listing."""
from __future__ import annotations

from app.infrastructure.llm.ollama_models import OLLAMA, list_models

__all__ = ["OLLAMA", "list_models"]
