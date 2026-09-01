"""Living Persona step D (slice 1) — TTS client for Elira's voice.

Thin client over the self-hosted Silero v4_ru HTTP service (CPU, LAN). The base URL is
`ELIRA_TTS_URL` (default the LAN server on :8005). Strictly local — audio never
leaves the network. Fully best-effort: status/list never raise; synthesize
raises only so the route can surface a clean error.
"""
from __future__ import annotations

import os

import requests


def tts_url() -> str:
    return os.environ.get("ELIRA_TTS_URL", "http://192.168.88.15:8005").strip().rstrip("/")


def tts_status() -> dict:
    base = tts_url()
    if not base:
        return {"ok": False, "configured": False, "voices": []}
    try:
        resp = requests.get(f"{base}/health", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return {"ok": True, "configured": True, "url": base, "voices": list(data.get("voices", []))}
    except Exception as exc:
        return {"ok": False, "configured": True, "url": base, "voices": [], "error": str(exc)}


def list_voices() -> list[str]:
    base = tts_url()
    if not base:
        return []
    try:
        resp = requests.get(f"{base}/voices", timeout=5)
        resp.raise_for_status()
        return list(resp.json().get("voices", []))
    except Exception:
        return []


def synthesize(text: str, voice: str | None = None) -> bytes:
    base = tts_url()
    if not base:
        raise RuntimeError("ELIRA_TTS_URL is not configured")
    payload: dict[str, str] = {"text": text}
    if voice:
        payload["voice"] = voice
    resp = requests.post(f"{base}/tts", json=payload, timeout=60)
    resp.raise_for_status()
    return resp.content


# ── STT (speech-to-text) — slice 2 ───────────────────────────────────────────
def stt_url() -> str:
    return os.environ.get("ELIRA_STT_URL", "http://192.168.88.15:8006").strip().rstrip("/")


def stt_status() -> dict:
    base = stt_url()
    if not base:
        return {"ok": False, "configured": False}
    try:
        resp = requests.get(f"{base}/health", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return {"ok": True, "configured": True, "url": base, "model": data.get("model")}
    except Exception as exc:
        return {"ok": False, "configured": True, "url": base, "error": str(exc)}


def transcribe(audio: bytes, filename: str = "audio", language: str | None = None, timeout: float = 120) -> str:
    base = stt_url()
    if not base:
        raise RuntimeError("ELIRA_STT_URL is not configured")
    files = {"file": (filename or "audio", audio, "application/octet-stream")}
    data: dict[str, str] = {}
    if language:
        data["language"] = language
    resp = requests.post(f"{base}/stt", files=files, data=data, timeout=timeout)
    resp.raise_for_status()
    return str(resp.json().get("text", ""))
