"""Image generation service — compatibility shim.

All logic lives in ``app.application.image_generation.runtime``.
Public API (generate_image, unload_model, get_status, OUTPUT_DIR) is
preserved for ``api/routes/image_routes.py`` and ``application/chat/auto_skills.py``.
"""
from __future__ import annotations

from app.application.image_generation.runtime import (
    OUTPUT_DIR,
    generate_image,
    get_status,
    unload_model,
)

__all__ = ["OUTPUT_DIR", "generate_image", "get_status", "unload_model"]
