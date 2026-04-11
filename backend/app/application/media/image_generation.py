from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import APP_DIR, IMAGE_MODEL_ID, OUTPUT_DIR
from app.core.llm import ask_model, clean_code_fence


def torch_gc() -> None:
    try:
        import gc

        gc.collect()
    except Exception:
        pass
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
    except Exception:
        pass


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text or "").strip()


def contains_cyrillic(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))


def prepare_image_prompt(
    prompt: str,
    model_name: str,
    auto_translate: bool = True,
    num_ctx: int = 2048,
) -> dict[str, str]:
    original = (prompt or "").strip()
    if not original:
        return {
            "ok": False,
            "original_prompt": "",
            "final_prompt": "",
            "translated": "false",
            "log": "Пустой prompt.",
        }

    if not auto_translate:
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": original,
            "translated": "false",
            "log": "Автоперевод отключён.",
        }

    if not contains_cyrillic(original):
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": original,
            "translated": "false",
            "log": "Кириллица не найдена — использую prompt как есть.",
        }

    try:
        translate_prompt = (
            "Преобразуй пользовательский запрос в короткий точный английский prompt "
            "для генерации изображения в SDXL Turbo. "
            "Сохрани смысл. Не добавляй пояснений, нумерации, markdown и кавычек. "
            "Верни только одну строку готового prompt.\n\n"
            f"Запрос пользователя:\n{original}"
        )
        translated = ask_model(
            model_name=model_name,
            profile_name="Аналитик",
            user_input=translate_prompt,
            include_history=False,
            num_ctx=num_ctx,
            temp=0.1,
        ).strip()
        translated = clean_code_fence(translated).strip().strip('"').strip("'")
        translated = re.sub(r"\s+", " ", translated)
        if not translated:
            raise ValueError("empty translation")
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": translated,
            "translated": "true",
            "log": f"RU → EN: {translated}",
        }
    except Exception as exc:
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": original,
            "translated": "fallback",
            "log": f"Перевод не сработал, использую исходный prompt. Ошибка: {exc}",
        }


def stop_ollama_model(model_name: str) -> dict[str, Any]:
    name = (model_name or "").strip()
    if not name:
        return {"ok": True, "message": "Модель не указана — пропускаю выгрузку."}
    if "cloud" in name.lower():
        return {"ok": True, "message": f"{name} — облачная модель, выгружать нечего."}

    try:
        proc = subprocess.run(
            ["ollama", "stop", name],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=str(APP_DIR),
        )
        stdout = strip_ansi(proc.stdout or "")
        stderr = strip_ansi(proc.stderr or "")
        if proc.returncode == 0:
            return {"ok": True, "message": stdout or f"Локальная модель {name} остановлена."}
        return {"ok": False, "message": stderr or stdout or f"Не удалось выгрузить модель {name}."}
    except FileNotFoundError:
        return {"ok": False, "message": "Команда ollama не найдена в PATH."}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def hf_access_hint(exc_text: str) -> str:
    low = (exc_text or "").lower()
    if any(
        marker in low
        for marker in [
            "gated",
            "401",
            "403",
            "access to model",
            "accept the conditions",
            "must be logged in",
        ]
    ):
        return (
            "Для FLUX.1-schnell нужно принять условия модели на Hugging Face "
            "и авторизоваться локально через `huggingface-cli login`."
        )
    return ""


def generate_image_sdxl_turbo(
    prompt: str,
    negative_prompt: str = "",
    model_name_to_unload: str = "",
    seed: int | None = None,
    width: int = 512,
    height: int = 512,
    num_inference_steps: int = 4,
    guidance_scale: float = 0.0,
    output_path: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Промпт пуст.", "path": "", "log": ""}

    logs: list[str] = []
    resolved_model_id = (model_id or IMAGE_MODEL_ID).strip()
    unload_info = stop_ollama_model(model_name_to_unload)
    logs.append(f"LLM unload: {unload_info['message']}")

    try:
        import torch
        from diffusers import AutoPipelineForText2Image
    except Exception as exc:
        return {
            "ok": False,
            "error": (
                "Не удалось импортировать diffusers/torch. "
                "Установи: pip install diffusers transformers accelerate safetensors"
            ),
            "path": "",
            "log": f"{logs[0]}\nImport error: {exc}",
        }

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    logs.append(f"Device: {device}, dtype: {dtype}")

    out_path = Path(output_path) if output_path else OUTPUT_DIR / f"image_{abs(hash(prompt)) % 10**10}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pipe = None
    try:
        torch_gc()
        fp16_kwargs: dict[str, Any] = {
            "torch_dtype": dtype,
            "use_safetensors": True,
        }
        if device == "cuda":
            fp16_kwargs["variant"] = "fp16"

        pipe = AutoPipelineForText2Image.from_pretrained(resolved_model_id, **fp16_kwargs)
        if device == "cuda":
            pipe.to("cuda")
            if hasattr(pipe, "enable_attention_slicing"):
                pipe.enable_attention_slicing()
            if hasattr(pipe, "enable_vae_slicing"):
                pipe.enable_vae_slicing()
        else:
            pipe.to(device)

        generator = None
        if seed is not None:
            generator = torch.Generator(device=device).manual_seed(int(seed))
            logs.append(f"Seed: {seed}")

        logs.append("Preset: SDXL Turbo quality defaults (512x512, 4 steps, guidance=0.0)")

        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "num_inference_steps": int(num_inference_steps),
            "guidance_scale": float(guidance_scale),
            "width": int(width),
            "height": int(height),
        }
        if negative_prompt.strip():
            kwargs["negative_prompt"] = negative_prompt.strip()
        if generator is not None:
            kwargs["generator"] = generator

        image = pipe(**kwargs).images[0]
        image.save(out_path)
        logs.append(f"Saved: {out_path}")
        return {
            "ok": True,
            "path": str(out_path),
            "log": "\n".join(logs),
            "model_id": resolved_model_id,
            "prompt": prompt,
        }
    except Exception as exc:
        logs.append(f"Generation error: {exc}")
        return {
            "ok": False,
            "error": str(exc),
            "path": "",
            "log": "\n".join(logs),
        }
    finally:
        try:
            del pipe
        except Exception:
            pass
        torch_gc()


def generate_image_flux_schnell(
    prompt: str,
    negative_prompt: str = "",
    model_name_to_unload: str = "",
    seed: int | None = None,
    width: int = 896,
    height: int = 512,
    num_inference_steps: int = 4,
    guidance_scale: float = 0.0,
    output_path: str | None = None,
    model_id: str | None = None,
    max_sequence_length: int = 160,
) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Промпт пуст.", "path": "", "log": ""}

    logs: list[str] = []
    resolved_model_id = (model_id or "black-forest-labs/FLUX.1-schnell").strip()
    unload_info = stop_ollama_model(model_name_to_unload)
    logs.append(f"LLM unload: {unload_info['message']}")

    try:
        import gc
        import os
        import torch
        from diffusers import FluxPipeline
    except Exception as exc:
        return {
            "ok": False,
            "error": (
                "Не удалось импортировать FLUX/diffusers. "
                "Установи: pip install -U diffusers transformers accelerate safetensors sentencepiece protobuf"
            ),
            "path": "",
            "log": f"{logs[0]}\nImport error: {exc}",
        }

    device = "cuda" if torch.cuda.is_available() else "cpu"
    flux_dtype = torch.bfloat16 if device == "cuda" else torch.float32
    logs.append(f"Device: {device}, dtype: {flux_dtype}")

    out_path = Path(output_path) if output_path else OUTPUT_DIR / f"flux_{abs(hash(prompt)) % 10**10}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pipe = None
    try:
        gc.collect()
        torch_gc()
        if device == "cuda":
            os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
            torch_gc()
            logs.append("CUDA cache очищен")

        pipe = FluxPipeline.from_pretrained(
            resolved_model_id,
            torch_dtype=flux_dtype,
            use_safetensors=True,
        )

        if device == "cuda":
            pipe.enable_model_cpu_offload()
            if hasattr(pipe, "enable_attention_slicing"):
                pipe.enable_attention_slicing()
                logs.append("Attention slicing enabled")
            if hasattr(pipe, "vae") and hasattr(pipe.vae, "enable_slicing"):
                pipe.vae.enable_slicing()
            if hasattr(pipe, "vae") and hasattr(pipe.vae, "enable_tiling"):
                pipe.vae.enable_tiling()
            logs.append("Offload: model_cpu_offload enabled")
        else:
            pipe.to("cpu")

        generator = None
        if seed is not None:
            generator = torch.Generator("cpu").manual_seed(int(seed))
            logs.append(f"Seed: {seed}")

        if negative_prompt.strip():
            logs.append("Note: negative prompt ignored for FLUX.1-schnell preset")

        logs.append(
            f"Preset: FLUX.1-schnell safe defaults ({int(width)}x{int(height)}, "
            f"{int(num_inference_steps)} steps, guidance={float(guidance_scale)}, max_seq={int(max_sequence_length)})"
        )

        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "guidance_scale": float(guidance_scale),
            "num_inference_steps": int(num_inference_steps),
            "max_sequence_length": int(max_sequence_length),
            "width": int(width),
            "height": int(height),
        }
        if generator is not None:
            kwargs["generator"] = generator

        image = pipe(**kwargs).images[0]
        image.save(out_path)
        logs.append(f"Saved: {out_path}")
        return {
            "ok": True,
            "path": str(out_path),
            "log": "\n".join(logs),
            "model_id": resolved_model_id,
            "prompt": prompt,
        }
    except Exception as exc:
        error_text = str(exc)
        hint = hf_access_hint(error_text)
        if hint:
            logs.append(hint)
        if "cuda out of memory" in error_text.lower():
            logs.append("Tip: для 8 GB VRAM попробуй ещё ниже: 768x512 и max_seq=128.")
        logs.append(f"Generation error: {error_text}")
        return {
            "ok": False,
            "error": hint or error_text,
            "path": "",
            "log": "\n".join(logs),
        }
    finally:
        try:
            del pipe
        except Exception:
            pass
        try:
            gc.collect()
        except Exception:
            pass
        torch_gc()
