"""Thin facade — all chat service logic lives in application/chat/chat_service.py."""
from app.application.chat.chat_service import (  # noqa: F401
    normalize_profile,
    run_chat,
    run_chat_stream,
)
