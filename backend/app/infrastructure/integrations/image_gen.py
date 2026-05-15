"""
image_gen.py вЂ” РіРµРЅРµСЂР°С†РёСЏ РёР·РѕР±СЂР°Р¶РµРЅРёР№ С‡РµСЂРµР· FLUX.1-schnell.

РћРїС‚РёРјРёР·РёСЂРѕРІР°РЅРѕ РґР»СЏ RTX 4060 Ti (8GB VRAM):
  вЂў torch.float16 РґР»СЏ СЌРєРѕРЅРѕРјРёРё РїР°РјСЏС‚Рё
  вЂў CPU offload РµСЃР»Рё РЅРµ С…РІР°С‚Р°РµС‚ VRAM
  вЂў РђРІС‚Рѕ-РѕС‡РёСЃС‚РєР° VRAM РїРѕСЃР»Рµ РіРµРЅРµСЂР°С†РёРё

РЈСЃС‚Р°РЅРѕРІРєР°:
  pip install diffusers transformers accelerate torch sentencepiece protobuf

РџРµСЂРІС‹Р№ Р·Р°РїСѓСЃРє СЃРєР°С‡Р°РµС‚ РјРѕРґРµР»СЊ (~12GB), РїРѕС‚РѕРј Р±СѓРґРµС‚ РєРµС€РёСЂРѕРІР°С‚СЊСЃСЏ.
"""
from __future__ import annotations
import gc
import logging
import time

from app.core.config import GENERATED_DIR

logger = logging.getLogger(__name__)

# torch РёРјРїРѕСЂС‚РёСЂСѓРµРј РЅР° СѓСЂРѕРІРЅРµ РјРѕРґСѓР»СЏ вЂ” Р±РµР·РѕРїР°СЃРЅРѕ
try:
    import torch
    _HAS_TORCH = True
except ImportError:
    torch = None
    _HAS_TORCH = False

OUTPUT_DIR = GENERATED_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_pipe = None
_model_id = "black-forest-labs/FLUX.1-schnell"


def _get_pipe():
    """Р›РµРЅРёРІР°СЏ Р·Р°РіСЂСѓР·РєР° РјРѕРґРµР»Рё."""
    global _pipe
    if _pipe is not None:
        return _pipe

    try:
        from diffusers import FluxPipeline
    except ImportError:
        raise ImportError("pip install diffusers transformers accelerate torch sentencepiece protobuf")

    logger.info(f"Loading FLUX.1-schnell (first time may take a few minutes)...")

    cuda_available = _HAS_TORCH and torch.cuda.is_available()
    dtype = torch.float16 if cuda_available else torch.float32
    logger.info("CUDA РґРѕСЃС‚СѓРїРЅР°: %s", cuda_available)

    _pipe = FluxPipeline.from_pretrained(
        _model_id,
        torch_dtype=dtype,
    )

    if cuda_available:
        try:
            _pipe.enable_model_cpu_offload()
            logger.info("CUDA + CPU offload enabled (saves VRAM)")
        except Exception:
            _pipe = _pipe.to("cuda")
            logger.info("CUDA (full GPU) enabled")
    else:
        _pipe = _pipe.to("cpu")
        logger.warning("Running on CPU вЂ” CUDA РЅРµРґРѕСЃС‚СѓРїРЅР°. РџРµСЂРµСѓСЃС‚Р°РЅРѕРІРё torch: pip install torch --index-url https://download.pytorch.org/whl/cu121")

    # РћРїС‚РёРјРёР·Р°С†РёРё
    try:
        _pipe.enable_attention_slicing()
    except Exception:
        pass

    return _pipe




def _clip_prompt(prompt: str, max_words: int = 60) -> str:
    """CLIP РїРѕРґРґРµСЂР¶РёРІР°РµС‚ РјР°РєСЃРёРјСѓРј 77 С‚РѕРєРµРЅРѕРІ (~60 СЃР»РѕРІ).
    РћР±СЂРµР·Р°РµРј РїСЂРѕРјРїС‚ С‡С‚РѕР±С‹ РёР·Р±РµР¶Р°С‚СЊ IndexError Рё truncation warning.
    """
    words = prompt.split()
    if len(words) <= max_words:
        return prompt
    clipped = " ".join(words[:max_words])
    logger.warning(f"РџСЂРѕРјРїС‚ РѕР±СЂРµР·Р°РЅ: {len(words)} СЃР»РѕРІ в†’ {max_words} (Р»РёРјРёС‚ CLIP 77 С‚РѕРєРµРЅРѕРІ)")
    return clipped


def generate_image(
    prompt: str,
    width: int = 768,
    height: int = 768,
    steps: int = 4,
    guidance_scale: float = 0.0,
    seed: int = -1,
    filename: str = "",
) -> dict:
    """
    Р“РµРЅРµСЂРёСЂСѓРµС‚ РёР·РѕР±СЂР°Р¶РµРЅРёРµ РїРѕ С‚РµРєСЃС‚РѕРІРѕРјСѓ РѕРїРёСЃР°РЅРёСЋ.

    FLUX.1-schnell РѕРїС‚РёРјР°Р»РµРЅ РЅР° 4 С€Р°РіР°С…, guidance_scale=0.0
    РњР°РєСЃРёРјСѓРј РґР»СЏ 8GB VRAM: 1024x1024
    """
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "РџСѓСЃС‚РѕР№ РїСЂРѕРјРїС‚"}

    # CLIP РѕР±СЂРµР·Р°РµС‚ РґРѕ 77 С‚РѕРєРµРЅРѕРІ вЂ” РѕР±СЂРµР·Р°РµРј Р·Р°СЂР°РЅРµРµ С‡С‚РѕР±С‹ РёР·Р±РµР¶Р°С‚СЊ РѕС€РёР±РѕРє
    prompt = _clip_prompt(prompt.strip())

    # РћРіСЂР°РЅРёС‡РµРЅРёСЏ РґР»СЏ 8GB VRAM
    max_pixels = 1024 * 1024
    if width * height > max_pixels:
        ratio = (max_pixels / (width * height)) ** 0.5
        width = int(width * ratio // 8) * 8
        height = int(height * ratio // 8) * 8

    # Р Р°Р·РјРµСЂС‹ РґРѕР»Р¶РЅС‹ Р±С‹С‚СЊ РєСЂР°С‚РЅС‹ 8
    width = (width // 8) * 8
    height = (height // 8) * 8

    try:
        if not _HAS_TORCH:
            return {"ok": False, "error": "torch РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅ: pip install torch"}

        pipe = _get_pipe()

        generator = None
        if seed >= 0:
            generator = torch.Generator("cpu").manual_seed(seed)

        logger.info(f"Generating: {width}x{height}, steps={steps}, prompt='{prompt[:60]}'")
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

        # РЎРѕС…СЂР°РЅСЏРµРј
        fname = filename or f"elira_img_{int(time.time())}.png"
        if not fname.endswith(".png"):
            fname += ".png"
        path = OUTPUT_DIR / fname
        image.save(str(path))

        # РћС‡РёСЃС‚РєР° VRAM
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

    except Exception as _oom_err:
        if 'out of memory' not in str(_oom_err).lower() and 'CUDA' not in str(_oom_err):
            _cleanup_vram()
            return {"ok": False, "error": str(_oom_err)}
        _cleanup_vram()
        return {"ok": False, "error": f"РќРµ С…РІР°С‚Р°РµС‚ VRAM РґР»СЏ {width}x{height}. РџРѕРїСЂРѕР±СѓР№ РјРµРЅСЊС€РёР№ СЂР°Р·РјРµСЂ (512x512)."}
    except Exception as e:
        _cleanup_vram()
        return {"ok": False, "error": str(e)}


def _cleanup_vram():
    """РћСЃРІРѕР±РѕР¶РґР°РµС‚ VRAM."""
    global _pipe
    try:
        del _pipe
        _pipe = None
        gc.collect()
        if _HAS_TORCH and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def unload_model() -> dict:
    """Р’С‹РіСЂСѓР¶Р°РµС‚ РјРѕРґРµР»СЊ РёР· РїР°РјСЏС‚Рё."""
    _cleanup_vram()
    return {"ok": True, "message": "РњРѕРґРµР»СЊ РІС‹РіСЂСѓР¶РµРЅР°, VRAM РѕСЃРІРѕР±РѕР¶РґРµРЅР°"}


def get_status() -> dict:
    """РЎС‚Р°С‚СѓСЃ РіРµРЅРµСЂР°С‚РѕСЂР°."""
    loaded = _pipe is not None
    info = {"ok": True, "model": _model_id, "loaded": loaded}

    try:
        if _HAS_TORCH and torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
            info["vram_total_mb"] = round(torch.cuda.get_device_properties(0).total_mem / 1024**2)
            info["vram_used_mb"] = round(torch.cuda.memory_allocated(0) / 1024**2)
            info["vram_free_mb"] = info["vram_total_mb"] - info["vram_used_mb"]
        else:
            info["gpu"] = "CPU only"
    except Exception:
        info["gpu"] = "unknown"

    return info

