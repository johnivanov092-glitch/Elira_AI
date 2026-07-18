"""Media resource routes (R1) — upload ≠ processing.

``POST /api/media/resources`` streams an uploaded file into the durable resource
store and returns ONLY a ResourceRef (resource_id, name, kind, content_type,
size). It performs NO STT/OCR/text-extraction/ffmpeg and puts nothing about the
file's CONTENT into any model context — the file just becomes an attached
resource that waits for a later, explicit ``resource_process`` tool call.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.application.media import resource_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media", tags=["media"])

_READ_CHUNK = 1024 * 1024
_MAX_SESSION_ID_CHARS = 128


@router.post("/resources")
async def upload_resource(file: UploadFile, session_id: str = Form(...)) -> JSONResponse:
    """Register the uploaded ORIGINAL as a durable resource, bound to *session_id*.
    Streaming + hard size cap + atomic rename live in the resource store; a failed
    intake unlinks its partial file. No processing happens here."""
    owner = str(session_id or "").strip()
    if (not owner or len(owner) > _MAX_SESSION_ID_CHARS
            or any(not ch.isprintable() for ch in owner)):
        raise HTTPException(status_code=400, detail="invalid session_id")

    intake = resource_store.new_intake()
    try:
        while True:
            chunk = await file.read(_READ_CHUNK)
            if not chunk:
                break
            intake.write(chunk)
        record = intake.commit(
            original_name=file.filename or "file",
            content_type=file.content_type or "application/octet-stream",
            owner_session=owner,
        )
    except resource_store.ResourceError as exc:
        intake.abort()
        raise HTTPException(status_code=exc.http_status, detail=exc.reason)
    except asyncio.CancelledError:
        intake.abort()
        raise
    except Exception as exc:  # noqa: BLE001 — never leak a partial file or raw path
        intake.abort()
        logger.error("resource upload failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="upload failed")

    # ResourceRef only — no absolute/storage path ever crosses this boundary.
    return JSONResponse(resource_store.resource_ref(record))
