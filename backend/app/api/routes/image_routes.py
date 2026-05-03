"""
image_routes.py — API генерации изображений.

POST /api/image/generate  — генерация по промпту
GET  /api/image/status    — статус GPU/модели
POST /api/image/unload    — выгрузить модель из VRAM
"""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/image", tags=["image-gen"])


class GenerateRequest(BaseModel):
    prompt: str
    width: int = 768
    height: int = 768
    steps: int = 4
    guidance_scale: float = 0.0
    seed: int = -1
    filename: str = ""


@router.post("/generate")
def api_generate(p: GenerateRequest):
    from app.application.media.flux_schnell_runtime import generate_image

    return generate_image(
        prompt=p.prompt, width=p.width, height=p.height,
        steps=p.steps, guidance_scale=p.guidance_scale,
        seed=p.seed, filename=p.filename,
    )


@router.get("/status")
def api_status():
    from app.application.media.flux_schnell_runtime import get_status

    return get_status()


@router.post("/unload")
def api_unload():
    from app.application.media.flux_schnell_runtime import unload_model

    return unload_model()
