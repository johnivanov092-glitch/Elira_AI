"""Compatibility facade for Ollama chat runtime."""
from __future__ import annotations

from app.application.chat.ollama_chat import normalize_profile, run_chat, run_chat_stream

__all__ = ["normalize_profile", "run_chat", "run_chat_stream"]
