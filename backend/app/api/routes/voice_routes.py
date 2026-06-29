"""Living Persona step D (slice 1) — voice (TTS) API.

POST /api/voice/tts {text, voice} -> audio/wav (synthesized by the self-hosted
Piper service). GET /api/voice/voices + /api/voice/status for the picker.
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.application.voice import runtime as voice_runtime

router = APIRouter(prefix="/api/voice", tags=["voice"])


class TTSRequest(BaseModel):
    text: str
    voice: str | None = None


@router.get("/status")
def voice_status():
    return JSONResponse(
        content=voice_runtime.tts_status(),
        media_type="application/json; charset=utf-8",
    )


@router.get("/stt-status")
def voice_stt_status():
    return JSONResponse(
        content=voice_runtime.stt_status(),
        media_type="application/json; charset=utf-8",
    )


@router.post("/stt")
async def voice_stt(file: UploadFile = File(...), language: str | None = Form(default=None)):
    """Transcribe uploaded audio -> {text} via the self-hosted whisper service."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    try:
        text = voice_runtime.transcribe(data, filename=file.filename or "audio", language=language)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"STT failed: {exc}")
    return JSONResponse(
        content={"ok": True, "text": text},
        media_type="application/json; charset=utf-8",
    )


@router.get("/voices")
def voice_voices():
    return JSONResponse(
        content={"ok": True, "voices": voice_runtime.list_voices()},
        media_type="application/json; charset=utf-8",
    )


@router.post("/tts")
def voice_tts(req: TTSRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    try:
        audio = voice_runtime.synthesize(text, req.voice)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS failed: {exc}")
    return Response(content=audio, media_type="audio/wav")
