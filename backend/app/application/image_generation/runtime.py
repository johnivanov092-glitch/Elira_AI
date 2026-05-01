"""Application-layer runtime for FLUX.1-schnell image generation.

Direct diffusers pipeline with lazy model loading, VRAM management,
and automatic CUDA/CPU fallback.

Optimised for RTX 4060 Ti (8 GB VRAM):
- torch.float16 for memory efficiency
- CPU offload if VRAM is tight
- Auto VRAM cleanup after generation

First run downloads the model (~12 GB), subsequent runs use local cache.
"""
from __future__ import annotations

import gc
import logging
import time

from app.core.config import GENERATED_DIR

logger = logging.getLogger(__name__)

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    torch = None  # type: ignore[assignment]
    _HAS_TORCH = False

OUTPUT_DIR = GENERATED_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_pipe = None
_MODEL_ID = "black-forest-labs/FLUX.1-schnell"


def _get_pipe():
    """Lazy-load FLUX.1-schnell pipeline on first call."""
    global _pipe
    if _pipe is not None:
        return _pipe

    try:
        from diffusers import FluxPipeline
    except ImportError:
        raise ImportError(
            "pip install diffusers transformers accelerate torch sentencepiece protobuf"
        )

    logger.info("Loading FLUX.1-schnell (first time may take a few minutes)...")

    cuda_available = _HAS_TORCH and torch.cuda.is_available()
    dtype = torch.float16 if cuda_available else torch.float32
    logger.info("CUDA available: %s", cuda_available)

    _pipe = FluxPipeline.from_pretrained(_MODEL_ID, torch_dtype=dtype)

    if cuda_available:
        try:
            _pipe.enable_model_cpu_offload()
            logger.info("CUDA + CPU offload enabled (saves VRAM)")
        except Exception:
            _pipe = _pipe.to("cuda")
            logger.info("CUDA (full GPU) enabled")
    else:
        _pipe = _pipe.to("cpu")
        logger.warning(
            "Running on CPU -- CUDA unavailable. "
            "Install: pip install torch --index-url https://download.pytorch.org/whl/cu121"
        )

    try:
        _pipe.enable_attention_slicing()
    except Exception:
        pass

    return _pipe


def _clip_prompt(prompt: str, max_words: int = 60) -> str:
    """Clip prompt to max_words to stay within the CLIP 77-token limit."""
    words = prompt.split()
    if len(words) <= max_words:
        return prompt
    clipped = " ".join(words[:max_words])
    logger.warning("Prompt clipped: %d words -> %d (CLIP 77-token limit)", len(words), max_words)
    return clipped


def _cleanup_vram() -> None:
    """Release the pipeline and free VRAM/RAM."""
    global _pipe
    try:
        del _pipe
        _pipe = None
        gc.collect()
        if _HAS_TORCH and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def generate_image(
    prompt: str,
    width: int = 768,
    height: int = 768,
    steps: int = 4,
    guidance_scale: float = 0.0,
    seed: int = -1,
    filename: str = "",
) -> dict:
    """Generate an image from a text prompt using FLUX.1-schnell.

    FLUX.1-schnell is optimised for 4 steps with guidance_scale=0.
    Maximum resolution for 8 GB VRAM: 1024x1024.
    """
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "Empty prompt"}

    prompt = _clip_prompt(prompt.strip())

    # Clamp total pixels to the 8 GB VRAM budget
    max_pixels = 1024 * 1024
    if width * height > max_pixels:
        ratio = (max_pixels / (width * height)) ** 0.5
        width = int(width * ratio // 8) * 8
        height = int(height * ratio // 8) * 8

    # Dimensions must be multiples of 8
    width = (width // 8) * 8
    height = (height // 8) * 8

    try:
        if not _HAS_TORCH:
            return {"ok": False, "error": "torch not installed: pip install torch"}

        pipe = _get_pipe()

        generator = None
        if seed >= 0:
            generator = torch.Generator("cpu").manual_seed(seed)

        logger.info(
            "Generating: %dx%d, steps=%d, prompt='%s'",
            width, height, steps, prompt[:60],
        )
        start = time.time()

        result = pipe(
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            generator=generator,
        )
        elapsed = round(time.time() - start, 1)
        image = result.images[0]

        fname = filename or f"elira_img_{int(time.time())}.png"
        if not fname.endswith(".png"):
            fname += ".png"
        path = OUTPUT_DIR / fname
        image.save(str(path))

        try:
            if _HAS_TORCH and torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()
        except Exception:
            pass

        return {
            "ok": True,
            "filename": fname,
            "path": str(path),
            "width": width,
            "height": height,
            "steps": steps,
            "seed": seed,
            "elapsed_sec": elapsed,
            "prompt": prompt,
            "view_url": f"/api/skills/view/{fname}",
            "download_url": f"/api/skills/download/{fname}",
        }

    except Exception as err:
        _cleanup_vram()
        msg = str(err)
        if "out of memory" in msg.lower() or "CUDA" in msg:
            return {
                "ok": False,
                "error": f"Insufficient VRAM for {width}x{height}. Try a smaller size (512x512).",
            }
        return {"ok": False, "error": msg}


def unload_model() -> dict:
    """Unload the model and free VRAM."""
    _cleanup_vram()
    return {"ok": True, "message": "Model unloaded, VRAM freed"}


def get_status() -> dict:
    """Return pipeline status and GPU info."""
    loaded = _pipe is not None
    info: dict = {"ok": True, "model": _MODEL_ID, "loaded": loaded}

    try:
        if _HAS_TORCH and torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
            info["vram_total_mb"] = round(
                torch.cuda.get_device_properties(0).total_mem / 1024 ** 2
            )
            info["vram_used_mb"] = round(torch.cuda.memory_allocated(0) / 1024 ** 2)
            info["vram_free_mb"] = info["vram_total_mb"] - info["vram_used_mb"]
        else:
            info["gpu"] = "CPU only"
    except Exception:
        info["gpu"] = "unknown"

    return info
