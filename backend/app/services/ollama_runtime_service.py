"""Ollama runtime service — compatibility shim.

All logic lives in ``app.application.ollama_runtime.runtime``.
Public API re-exported for all callers: api/routes/elira_state.
"""
from __future__ import annotations

from app.application.ollama_runtime.runtime import list_ollama_models

__all__ = ["list_ollama_models"]
