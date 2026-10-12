"""/api/telegram — Telegram bot settings for the UI (Settings → Telegram).

The bot token is stored in the portable vault; this API only binds its opaque
secret_ref. Sending messages from a run is the agent's on-demand `telegram` tool.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class TelegramConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_token_ref: str


class TelegramUserAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: bool


def _status() -> dict:
    from app.application import telegram
    from app.application.telegram.store import get_config_value, list_telegram_users, owner_chat_id
    from app.infrastructure.secrets import vault

    config = telegram.get_telegram_config()
    status = telegram.telegram_bot_status()
    try:
        vault_state = vault.status()
    except Exception:
        vault_state = {"initialized": True, "locked": True}
    owner_id = owner_chat_id()
    users = list_telegram_users().get("users") or []
    owner = next((user for user in users if user.get("chat_id") == owner_id), None)
    return {
        "ok": True,
        "running": bool(status.get("running")),
        "stopping": bool(status.get("stopping")),
        "has_token": bool(config.get("has_token")),
        "token_preview": str(config.get("bot_token") or ""),
        "legacy_token_present": bool(config.get("legacy_token_present")),
        "enabled": get_config_value("enabled", "") == "true",
        "vault_initialized": bool(vault_state.get("initialized")),
        "vault_locked": bool(vault_state.get("initialized")) and bool(vault_state.get("locked")),
        "owner": ({"chat_id": owner_id, "username": (owner or {}).get("username", ""),
                   "first_name": (owner or {}).get("first_name", "")} if owner_id else None),
    }


@router.get("/status")
def telegram_status():
    return _status()


@router.post("/config")
def telegram_config(body: TelegramConfigRequest):
    from app.application import telegram

    ref = body.bot_token_ref.strip()
    if not ref.startswith("sref_"):
        raise HTTPException(400, "bot_token_ref must be an opaque sref_ reference")
    telegram.update_telegram_config({"bot_token_ref": ref})
    return _status()


@router.post("/start")
def telegram_start():
    from app.application import telegram

    result = telegram.start_telegram_bot()
    if not result.get("ok"):
        raise HTTPException(409, str(result.get("error") or "Telegram bot did not start"))
    return {**_status(), "bot_username": result.get("bot_username", "")}


@router.post("/stop")
def telegram_stop():
    from app.application import telegram

    telegram.stop_telegram_bot()
    return _status()


@router.post("/test")
def telegram_test():
    from app.application import telegram

    result = telegram.test_telegram_connection()
    if not result.get("ok"):
        raise HTTPException(409, str(result.get("error") or "Telegram connection failed"))
    return {"ok": True, "bot_username": result.get("bot_username", ""), "bot_name": result.get("bot_name", "")}


@router.post("/pair")
def telegram_pair():
    """A one-time code: the person sends /pair <code> to the bot and becomes its owner."""
    from app.application.telegram.store import create_pair_code

    return create_pair_code()


@router.get("/users")
def telegram_users():
    from app.application import telegram

    return telegram.list_telegram_users()


@router.post("/users/{chat_id}")
def telegram_user_access(chat_id: int, body: TelegramUserAccessRequest):
    from app.application import telegram

    return telegram.toggle_user_access(chat_id, body.allowed)
