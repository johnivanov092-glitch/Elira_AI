"""API роуты для Telegram-бот интеграции Elira AI."""
from fastapi import APIRouter
from pydantic import BaseModel

from app.application.integrations.telegram_service import (
    get_telegram_config,
    update_telegram_config,
    telegram_bot_status,
    start_telegram_bot,
    stop_telegram_bot,
    test_telegram_connection,
    list_telegram_users,
    toggle_user_access,
    get_telegram_log,
)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class TelegramConfigRequest(BaseModel):
    bot_token: str | None = None
    model: str | None = None
    profile: str | None = None
    allowed_users: str | None = None
    max_message_length: int | None = None
    use_memory: bool | None = None
    use_web_search: bool | None = None
    welcome_message: str | None = None


class ToggleUserRequest(BaseModel):
    chat_id: int
    allowed: bool


@router.get("/config")
def api_get_config():
    return get_telegram_config()


@router.put("/config")
def api_update_config(req: TelegramConfigRequest):
    data = {k: v for k, v in req.dict().items() if v is not None}
    return update_telegram_config(data)


@router.get("/status")
def api_status():
    return telegram_bot_status()


@router.post("/start")
def api_start():
    return start_telegram_bot()


@router.post("/stop")
def api_stop():
    return stop_telegram_bot()


@router.get("/test")
def api_test():
    return test_telegram_connection()


@router.get("/users")
def api_users():
    return list_telegram_users()


@router.post("/users/toggle")
def api_toggle_user(req: ToggleUserRequest):
    return toggle_user_access(req.chat_id, req.allowed)


@router.get("/log")
def api_log(limit: int = 50):
    return get_telegram_log(limit)
