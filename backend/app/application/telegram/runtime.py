from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import requests

from app.application.telegram.store import (
    get_config_value,
    log_message,
    register_user,
    is_user_allowed,
    set_config_value,
)


logger = logging.getLogger(__name__)

TG_API = "https://api.telegram.org/bot{token}"
DEFAULT_PROFILE = "Универсальный"
DEFAULT_WELCOME_MESSAGE = "Привет! Я Elira — твоя AI-ассистентка 🤖✨\nПиши мне что угодно!"

_bot_thread: threading.Thread | None = None
_running = False
_last_update_id = 0


def get_telegram_config() -> dict[str, Any]:
    token = get_config_value("bot_token", "")
    return {
        "ok": True,
        "bot_token": token[:8] + "..." + token[-4:] if len(token) > 12 else ("***" if token else ""),
        "has_token": bool(token),
        "model": get_config_value("model", ""),
        "profile": get_config_value("profile", DEFAULT_PROFILE),
        "allowed_users": get_config_value("allowed_users", "all"),
        "max_message_length": int(get_config_value("max_message_length", "4000")),
        "use_memory": get_config_value("use_memory", "true") == "true",
        "use_web_search": get_config_value("use_web_search", "false") == "true",
        "running": _running,
        "welcome_message": get_config_value("welcome_message", DEFAULT_WELCOME_MESSAGE),
    }


def tg_request(
    method: str,
    token: str,
    data: dict[str, Any] | None = None,
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    url = f"{TG_API.format(token=token)}/{method}"
    try:
        response = requests.post(url, json=data or {}, timeout=timeout)
        return response.json()
    except Exception as exc:
        logger.error("TG API error [%s]: %s", method, exc)
        return {"ok": False, "description": str(exc)}


def send_message(
    token: str,
    chat_id: int,
    text: str,
    parse_mode: str = "Markdown",
) -> dict[str, Any]:
    max_len = int(get_config_value("max_message_length", "4000"))
    if len(text) > max_len:
        text = text[:max_len] + "\n\n_(сообщение обрезано)_"

    result = tg_request(
        "sendMessage",
        token,
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        },
    )
    if not result.get("ok") and parse_mode:
        result = tg_request(
            "sendMessage",
            token,
            {
                "chat_id": chat_id,
                "text": text,
            },
        )
    return result


def send_typing(token: str, chat_id: int) -> None:
    tg_request(
        "sendChatAction",
        token,
        {"chat_id": chat_id, "action": "typing"},
        timeout=5,
    )


def test_telegram_connection() -> dict[str, Any]:
    token = get_config_value("bot_token", "")
    if not token:
        return {"ok": False, "error": "Токен бота не задан"}

    result = tg_request("getMe", token, timeout=10)
    if result.get("ok"):
        bot = result["result"]
        return {
            "ok": True,
            "bot_username": bot.get("username", ""),
            "bot_name": bot.get("first_name", ""),
            "bot_id": bot.get("id", 0),
        }
    return {"ok": False, "error": result.get("description", "Неизвестная ошибка")}


def process_message(token: str, message: dict[str, Any]) -> None:
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    user = message.get("from", {})
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return

    register_user(
        chat_id,
        username=user.get("username", ""),
        first_name=user.get("first_name", ""),
        last_name=user.get("last_name", ""),
    )

    if not is_user_allowed(chat_id):
        send_message(token, chat_id, "⛔ Доступ ограничен. Обратитесь к администратору.", "")
        return

    log_message(chat_id, "in", text)

    if text.startswith("/"):
        handle_command(token, chat_id, text)
        return

    send_typing(token, chat_id)

    try:
        model = get_config_value("model", "")
        profile = get_config_value("profile", DEFAULT_PROFILE)
        use_memory = get_config_value("use_memory", "true") == "true"
        use_web = get_config_value("use_web_search", "false") == "true"

        from app.services.agents_service import run_agent

        result = run_agent(
            model_name=model or "gemma3:4b",
            profile_name=profile,
            user_input=text,
            use_memory=use_memory,
            use_web_search=use_web,
        )
        answer = result.get("answer", "Не удалось получить ответ 😔")
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()

        log_message(chat_id, "out", answer)
        send_message(token, chat_id, answer)
    except Exception as exc:
        logger.error("TG process error: %s", exc)
        send_message(token, chat_id, f"⚠️ Ошибка: {str(exc)[:200]}", "")


def handle_command(token: str, chat_id: int, text: str) -> None:
    cmd = text.split()[0].lower().split("@")[0]

    if cmd == "/start":
        welcome = get_config_value("welcome_message", DEFAULT_WELCOME_MESSAGE)
        send_message(token, chat_id, welcome, "")
        log_message(chat_id, "cmd", "/start")
        return

    if cmd == "/help":
        help_text = (
            "🤖 *Elira AI — Telegram Bot*\n\n"
            "Просто напиши мне сообщение и я отвечу!\n\n"
            "*Команды:*\n"
            "/start — Приветствие\n"
            "/help — Справка\n"
            "/status — Мой статус\n"
            "/web on|off — Веб-поиск\n"
            "/memory on|off — Память\n\n"
            "💡 Я могу искать в интернете, помогать с кодом, "
            "анализировать текст и многое другое!"
        )
        send_message(token, chat_id, help_text)
        log_message(chat_id, "cmd", "/help")
        return

    if cmd == "/status":
        model = get_config_value("model", "auto")
        profile = get_config_value("profile", DEFAULT_PROFILE)
        web = get_config_value("use_web_search", "false")
        memory = get_config_value("use_memory", "true")
        status = (
            f"📊 *Статус Elira*\n\n"
            f"🧠 Модель: `{model}`\n"
            f"👤 Профиль: {profile}\n"
            f"🌐 Веб-поиск: {'✅' if web == 'true' else '❌'}\n"
            f"💾 Память: {'✅' if memory == 'true' else '❌'}"
        )
        send_message(token, chat_id, status)
        return

    if cmd == "/web":
        parts = text.split()
        if len(parts) > 1 and parts[1].lower() in ("on", "off"):
            enabled = parts[1].lower() == "on"
            set_config_value("use_web_search", "true" if enabled else "false")
            send_message(
                token,
                chat_id,
                f"🌐 Веб-поиск: {'✅ включён' if enabled else '❌ выключен'}",
                "",
            )
        else:
            send_message(token, chat_id, "Использование: /web on или /web off", "")
        return

    if cmd == "/memory":
        parts = text.split()
        if len(parts) > 1 and parts[1].lower() in ("on", "off"):
            enabled = parts[1].lower() == "on"
            set_config_value("use_memory", "true" if enabled else "false")
            send_message(
                token,
                chat_id,
                f"💾 Память: {'✅ включена' if enabled else '❌ выключена'}",
                "",
            )
        else:
            send_message(token, chat_id, "Использование: /memory on или /memory off", "")
        return

    send_message(token, chat_id, "Неизвестная команда. Напиши /help для справки.", "")


def poll_loop() -> None:
    global _running, _last_update_id

    token = get_config_value("bot_token", "")
    if not token:
        logger.error("Telegram bot: нет токена")
        _running = False
        return

    logger.info("Telegram bot polling started")

    while _running:
        try:
            result = tg_request(
                "getUpdates",
                token,
                {
                    "offset": _last_update_id + 1,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                },
                timeout=35,
            )

            if not result.get("ok"):
                logger.warning("TG getUpdates error: %s", result.get("description", "?"))
                time.sleep(5)
                continue

            for update in result.get("result", []):
                _last_update_id = update["update_id"]
                message = update.get("message")
                if not message:
                    continue
                try:
                    process_message(token, message)
                except Exception as exc:
                    logger.error("TG message processing error: %s", exc)

        except requests.exceptions.Timeout:
            continue
        except Exception as exc:
            logger.error("TG poll error: %s", exc)
            time.sleep(5)

    logger.info("Telegram bot polling stopped")


def start_telegram_bot() -> dict[str, Any]:
    global _running, _bot_thread

    if _running:
        return {"ok": True, "status": "already_running"}

    token = get_config_value("bot_token", "")
    if not token:
        return {
            "ok": False,
            "error": "Токен бота не задан. Укажите bot_token в настройках.",
        }

    test = test_telegram_connection()
    if not test.get("ok"):
        return {
            "ok": False,
            "error": f"Не удалось подключиться: {test.get('error', '?')}",
        }

    _running = True
    _bot_thread = threading.Thread(target=poll_loop, daemon=True, name="telegram-bot")
    _bot_thread.start()

    return {
        "ok": True,
        "status": "started",
        "bot_username": test.get("bot_username", ""),
        "bot_name": test.get("bot_name", ""),
    }


def stop_telegram_bot() -> dict[str, Any]:
    global _running, _bot_thread

    _running = False
    if _bot_thread and _bot_thread.is_alive():
        _bot_thread.join(timeout=5)
    _bot_thread = None

    return {"ok": True, "status": "stopped"}


def telegram_bot_status() -> dict[str, Any]:
    config = get_telegram_config()
    return {
        "ok": True,
        "running": _running,
        "has_token": config.get("has_token", False),
        "bot_token_preview": config.get("bot_token", ""),
    }
