from app.application.telegram.store import (
    DB_PATH,
    get_telegram_log,
    list_telegram_users,
    toggle_user_access,
    update_telegram_config,
)

__all__ = [
    "DB_PATH",
    "DEFAULT_PROFILE",
    "DEFAULT_WELCOME_MESSAGE",
    "get_telegram_config",
    "get_telegram_log",
    "list_telegram_users",
    "send_telegram_message",
    "start_telegram_bot",
    "stop_telegram_bot",
    "telegram_bot_status",
    "test_telegram_connection",
    "toggle_user_access",
    "update_telegram_config",
]

_RUNTIME_EXPORTS = frozenset(['DEFAULT_PROFILE', 'DEFAULT_WELCOME_MESSAGE', 'get_telegram_config', 'send_telegram_message', 'start_telegram_bot', 'stop_telegram_bot', 'telegram_bot_status', 'test_telegram_connection'])

def __getattr__(name: str):
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(name)
    from app.application.skill_services import telegram_runtime
    return getattr(telegram_runtime, name)
