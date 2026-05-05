"""API роуты для Telegram-бот интеграции Elira AI."""
from fastapi import APIRouter
from pydantic import BaseModel

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
    from app.application.telegram import get_telegram_config

    return get_telegram_config()


@router.put("/config")
def api_update_config(req: TelegramConfigRequest):
    from app.application.telegram import update_telegram_config

    data = {k: v for k, v in req.dict().items() if v is not None}
    return update_telegram_config(data)


@router.get("/status")
def api_status():
    from app.application.telegram import telegram_bot_status

    return telegram_bot_status()


@router.post("/start")
def api_start():
    from app.application.telegram import start_telegram_bot

    return start_telegram_bot()


@router.post("/stop")
def api_stop():
    from app.application.telegram import stop_telegram_bot

    return stop_telegram_bot()


@router.get("/test")
def api_test():
    from app.application.telegram import test_telegram_connection

    return test_telegram_connection()


@router.get("/users")
def api_users():
    from app.application.telegram import list_telegram_users

    return list_telegram_users()


@router.post("/users/toggle")
def api_toggle_user(req: ToggleUserRequest):
    from app.application.telegram import toggle_user_access

    return toggle_user_access(req.chat_id, req.allowed)


@router.get("/log")
def api_log(limit: int = 50):
    from app.application.telegram import get_telegram_log

    return get_telegram_log(limit)
