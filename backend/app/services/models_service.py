"""Thin facade — all models service logic lives in infrastructure/models/models_service.py."""
from app.infrastructure.models.models_service import (  # noqa: F401
    OLLAMA_TAGS_URL,
    get_models,
)
