"""API роуты для Telegram-бот интеграции Elira AI."""
from fastapi import APIRouter, HTTPException
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


# ── Approval inbox ─────────────────────────────────────────────────────────────

class ApprovalCallbackRequest(BaseModel):
    approval_id: str
    action: str  # "approve" | "reject"
    chat_id: int | None = None


@router.post("/approval_callback", summary="Receive approve/reject from Telegram bot")
def api_approval_callback(req: ApprovalCallbackRequest):
    """Webhook called by the Telegram bot when user sends /approve or /reject.

    Also usable directly (e.g. from a custom bot integration) to act on
    a pending tool-call approval without going through the Telegram UI.
    """
    from app.application.monitoring import runtime as mon

    if req.action not in ("approve", "reject"):
        raise HTTPException(400, f"Invalid action '{req.action}': must be 'approve' or 'reject'")

    item = mon.get_approval(req.approval_id)
    if not item:
        raise HTTPException(404, f"Approval '{req.approval_id}' not found")
    if item["status"] != "pending":
        raise HTTPException(400, f"Approval is not pending (status: '{item['status']}')")

    new_status = "approved" if req.action == "approve" else "rejected"
    result = mon.update_approval_status(req.approval_id, status=new_status)
    return {
        "ok": True,
        "approval_id": req.approval_id,
        "action": req.action,
        "status": new_status,
        "tool_name": item.get("tool_name"),
    }


@router.post("/set_admin_chat", summary="Set the admin chat_id for approval notifications")
def api_set_admin_chat(chat_id: int):
    """Store the Telegram chat_id that receives approval notifications.

    Call once with the admin/operator chat_id after configuring the bot token.
    """
    from app.application.telegram.store import set_config_value
    set_config_value("admin_chat_id", str(chat_id))
    return {"ok": True, "admin_chat_id": chat_id}
