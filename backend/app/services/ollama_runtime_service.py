"""Compatibility facade for Ollama runtime model listing."""
from __future__ import annotations

from app.infrastructure.llm.ollama_models import list_ollama_models

__all__ = ["list_ollama_models"]
