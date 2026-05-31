"""Compatibility facade for local FLUX image generation runtime."""
from __future__ import annotations

from app.application.media.flux_schnell_runtime import (
    generate_image,
    get_status,
    unload_model,
)

__all__ = [
    "generate_image",
    "get_status",
    "unload_model",
]
