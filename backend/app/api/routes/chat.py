"""Attachment parsing route for the workspace chat composer.

Generation now runs through ``/api/code-agent/*``. The only remaining
``/api/chat`` surface is ``/attach``, which extracts text from uploaded files
before the frontend sends that text to the code-agent stream.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.application.file_extract.runtime import extract_file
from app.infrastructure.llm.vision_ocr import describe_image, is_vision_enabled

router = APIRouter(prefix="/api/chat", tags=["chat"])

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MAX_ATTACH_BYTES = 25 * 1024 * 1024


def _attach_result(
    *, filename: str, kind: str, text: str, note: str = "", ok: bool = True
) -> dict[str, Any]:
    return {
        "ok": ok,
        "filename": filename,
        "kind": kind,
        "text": text,
        "chars": len(text),
        "note": note,
    }


def _json_attach(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(payload),
        media_type="application/json; charset=utf-8",
    )


@router.post("/attach")
async def chat_attach(file: UploadFile) -> JSONResponse:
    filename = file.filename or "файл"
    ext = Path(filename).suffix.lower()
    try:
        contents = await file.read()
    except Exception as exc:
        return _json_attach(
            _attach_result(
                filename=filename,
                kind="document",
                text="",
                ok=False,
                note=f"Не удалось прочитать файл: {exc}",
            ),
            status_code=400,
        )

    if not contents:
        return _json_attach(
            _attach_result(filename=filename, kind="document", text="", ok=False, note="Пустой файл"),
            status_code=400,
        )

    if len(contents) > _MAX_ATTACH_BYTES:
        return _json_attach(
            _attach_result(
                filename=filename,
                kind="document",
                text="",
                ok=False,
                note=f"Файл больше {_MAX_ATTACH_BYTES // (1024 * 1024)} МБ",
            ),
            status_code=413,
        )

    if ext in _IMAGE_EXTS:
        if not is_vision_enabled():
            return _json_attach(
                _attach_result(
                    filename=filename,
                    kind="image",
                    text="",
                    ok=False,
                    note="Распознавание картинок отключено на сервере (VISION_ENABLED).",
                )
            )
        description = describe_image(filename, contents)
        if not description:
            return _json_attach(
                _attach_result(
                    filename=filename,
                    kind="image",
                    text="",
                    ok=False,
                    note="Не удалось распознать изображение.",
                )
            )
        return _json_attach(_attach_result(filename=filename, kind="image", text=description))

    extracted = extract_file(filename, contents)
    text = str(extracted.get("text") or "")
    stripped = text.strip()
    if not stripped:
        return _json_attach(
            _attach_result(
                filename=filename,
                kind="document",
                text="",
                ok=False,
                note="В файле не найдено текста.",
            )
        )
    if stripped.startswith("[") and (
        "ошибка" in stripped.lower() or "не установлен" in stripped.lower()
    ):
        return _json_attach(
            _attach_result(
                filename=filename,
                kind="document",
                text="",
                ok=False,
                note=stripped.strip("[]"),
            )
        )
    return _json_attach(_attach_result(filename=filename, kind="document", text=text))
