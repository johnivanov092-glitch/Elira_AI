"""Thin facade — all image generation logic lives in infrastructure/integrations/image_gen.py."""
from app.infrastructure.integrations.image_gen import (  # noqa: F401
    OUTPUT_DIR,
    _cleanup_vram,
    _clip_prompt,
    _get_pipe,
    generate_image,
    get_status,
    unload_model,
)
