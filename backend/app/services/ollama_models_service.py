"""Thin facade — all ollama models service logic lives in infrastructure/models/ollama_models_service.py."""
from app.infrastructure.models.ollama_models_service import (  # noqa: F401
    OLLAMA,
    list_models,
)
