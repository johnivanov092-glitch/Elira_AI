"""
image_gen.py — генерация изображений через FLUX.1-schnell.

Оптимизировано для RTX 4060 Ti (8GB VRAM):
  • torch.float16 для экономии памяти
  • CPU offload если не хватает VRAM
  • Авто-очистка VRAM после генерации

Установка:
  pip install diffusers transformers accelerate torch sentencepiece protobuf

Первый запуск скачает модель (~12GB), потом будет кешироваться.
"""
from __future__ import annotations
import gc
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# torch импортируем на уровне модуля — безопасно
try:
    import torch
    _HAS_TORCH = True
except ImportError:
    torch = None
    _HAS_TORCH = False

OUTPUT_DIR = Path("data/generated")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_pipe = None
_model_id = "black-forest-labs/FLUX.1-schnell"


def _get_pipe():
    """Ленивая загрузка модели."""
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
    logger.info("CUDA доступна: %s", cuda_available)

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
        logger.warning("Running on CPU — CUDA недоступна. Переустанови torch: pip install torch --index-url https://download.pytorch.org/whl/cu121")

    # Оптимизации
    try:
        _pipe.enable_attention_slicing()
    except Exception:
        pass

    return _pipe




def _clip_prompt(prompt: str, max_words: int = 60) -> str:
    """CLIP поддерживает максимум 77 токенов (~60 слов).
    Обрезаем промпт чтобы избежать IndexError и truncation warning.
    """
    words = prompt.split()
    if len(words) <= max_words:
        return prompt
    clipped = " ".join(words[:max_words])
    logger.warning(f"Промпт обрезан: {len(words)} слов → {max_words} (лимит CLIP 77 токенов)")
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
    Генерирует изображение по текстовому описанию.

    FLUX.1-schnell оптимален на 4 шагах, guidance_scale=0.0
    Максимум для 8GB VRAM: 1024x1024
    """
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "Пустой промпт"}

    # CLIP обрезает до 77 токенов — обрезаем заранее чтобы избежать ошибок
    prompt = _clip_prompt(prompt.strip())

    # Ограничения для 8GB VRAM
    max_pixels = 1024 * 1024
    if width * height > max_pixels:
        ratio = (max_pixels / (width * height)) ** 0.5
        width = int(width * ratio // 8) * 8
        height = int(height * ratio // 8) * 8

    # Размеры должны быть кратны 8
    width = (width // 8) * 8
    height = (height // 8) * 8

    try:
        if not _HAS_TORCH:
            return {"ok": False, "error": "torch не установлен: pip install torch"}

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

        # Сохраняем
        fname = filename or f"elira_img_{int(time.time())}.png"
        if not fname.endswith(".png"):
            fname += ".png"
        path = OUTPUT_DIR / fname
        image.save(str(path))

        # Очистка VRAM
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
        return {"ok": False, "error": f"Не хватает VRAM для {width}x{height}. Попробуй меньший размер (512x512)."}
    except Exception as e:
        _cleanup_vram()
        return {"ok": False, "error": str(e)}


def _cleanup_vram():
    """Освобождает VRAM."""
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
    """Выгружает модель из памяти."""
    _cleanup_vram()
    return {"ok": True, "message": "Модель выгружена, VRAM освобождена"}


def get_status() -> dict:
    """Статус генератора."""
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
