"""Telegram as an on-demand agent tool: send a message or read the bot log.

Bot setup (token, start/stop, users) lives in Settings → Telegram. The token is
resolved inside the Telegram runtime from the vault; it is never an argument.
"""
from __future__ import annotations

from typing import Any

from app.application.code_agent.tools._runtime_control_contract import (
    input_request,
    run_operation,
    secret_request,
)


def _send(chat_id: int | None, text: str, parse_mode: str) -> dict[str, Any]:
    from app.application import telegram
    from app.application.telegram.store import get_config_value
    from app.infrastructure.secrets import vault

    if chat_id is None:
        raise input_request("Укажите Telegram chat_id.", "chat_id", "Telegram chat ID")
    if not text.strip():
        raise input_request("Укажите текст Telegram-сообщения.", "text", "Текст сообщения")
    token_ref = get_config_value("bot_token_ref", "").strip()
    if not token_ref:
        raise secret_request("Для отправки в Telegram нужен bot token (Настройки → Telegram).")
    if vault.status().get("locked"):
        raise secret_request(
            "Разблокируйте хранилище секретов для отправки в Telegram.",
            existing_secret_ref=token_ref,
        )
    return telegram.send_telegram_message(chat_id=int(chat_id), text=text, parse_mode=parse_mode or "Markdown")


def tool_telegram(
    *,
    action: str,
    chat_id: int | None = None,
    text: str = "",
    parse_mode: str = "Markdown",
    limit: int = 50,
) -> dict[str, Any]:
    """Send a Telegram message or read recent bot messages."""
    act = str(action or "").strip().lower()
    if act == "send":
        return run_operation("telegram_send", lambda: _send(chat_id, str(text or ""), str(parse_mode or "")))
    if act == "messages":
        from app.application import telegram

        return run_operation("telegram_messages", lambda: telegram.get_telegram_log(
            limit=min(max(1, int(limit or 50)), 500),
            chat_id=int(chat_id) if chat_id is not None else None,
        ))
    return {"ok": False, "error": "unknown_action", "text": "ERROR: action должен быть send или messages."}
