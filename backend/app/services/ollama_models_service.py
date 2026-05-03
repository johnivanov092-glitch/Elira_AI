# DEPRECATED: duplicates models_service.py / application/ollama_models/runtime.py
# Nobody imports this file. Kept as a shim for backward-compat only.
from __future__ import annotations

from app.application.ollama_models.runtime import get_models as list_models  # noqa: F401

__all__ = ["list_models"]
