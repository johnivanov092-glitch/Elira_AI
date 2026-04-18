"""
telegram_service.py — Telegram-бот интеграция Elira AI.

Лёгкий long-polling бот без внешних зависимостей (только requests).
Получает сообщения → отправляет в LLM → возвращает ответ.
Поддержка: текст, голосовые (если Whisper), фото-описания.

Настройки хранятся в SQLite.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import re
from datetime import datetime
from typing import Any

import requests

from app.core.config import DATA_DIR
from app.infrastructure.db.connection import connect_sqlite

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "integrations.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_bot_thread: threading.Thread | None = None
_running = False
_last_update_id = 0

TG_API = "https://api.telegram.org/bot{token}"


# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════

def _connect():
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def _init_db():
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS telegram_config (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS telegram_users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                allowed INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS telegram_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                direction TEXT,
                text TEXT,
                created_at TEXT
            );
        """)
        conn.commit()
    finally:
        conn.close()


_init_db()


# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

def _get_config(key: str, default: str = "") -> str:
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM telegram_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def _set_config(key: str, value: str):
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO telegram_config (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_telegram_config() -> dict:
    """Получить всю конфигурацию Telegram-бота."""
    token = _get_config("bot_token", "")
    return {
        "ok": True,
        "bot_token": token[:8] + "..." + token[-4:] if len(token) > 12 else ("***" if token else ""),
        "has_token": bool(token),
        "model": _get_config("model", ""),
        "profile": _get_config("profile", "Универсальный"),
        "allowed_users": _get_config("allowed_users", "all"),
        "max_message_length": int(_get_config("max_message_length", "4000")),
        "use_memory": _get_config("use_memory", "true") == "true",
        "use_web_search": _get_config("use_web_search", "false") == "true",
        "running": _running,
        "welcome_message": _get_config("welcome_message", "Привет! Я Elira — твоя AI-ассистентка 🤖✨\nПиши мне что угодно!"),
    }


def update_telegram_config(data: dict) -> dict:
    """Обновить конфигурацию."""
    allowed_keys = {
        "bot_token", "model", "profile", "allowed_users",
        "max_message_length", "use_memory", "use_web_search", "welcome_message",
    }
    updated = []
    for k, v in data.items():
        if k not in allowed_keys:
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        _set_config(k, str(v))
        updated.append(k)
    return {"ok": True, "updated": updated}


# ═══════════════════════════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════════════════════════

def _register_user(chat_id: int, username: str = "", first_name: str = "", last_name: str = ""):
    conn = _connect()
    try:
        existing = conn.execute("SELECT chat_id FROM telegram_users WHERE chat_id = ?", (chat_id,)).fetchone()
        if not existing:
            allowed_cfg = _get_config("allowed_users", "all")
            auto_allow = 1 if allowed_cfg == "all" else 0
            conn.execute(
                "INSERT INTO telegram_users (chat_id, username, first_name, last_name, allowed, created_at) VALUES (?,?,?,?,?,?)",
                (chat_id, username, first_name, last_name, auto_allow, datetime.utcnow().isoformat()),
            )
            conn.commit()
    finally:
        conn.close()


def _is_user_allowed(chat_id: int) -> bool:
    allowed_cfg = _get_config("allowed_users", "all")
    if allowed_cfg == "all":
        return True
    conn = _connect()
    try:
        row = conn.execute("SELECT allowed FROM telegram_users WHERE chat_id = ?", (chat_id,)).fetchone()
        return bool(row and row["allowed"])
    finally:
        conn.close()


def list_telegram_users() -> dict:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM telegram_users ORDER BY created_at DESC").fetchall()
        return {"ok": True, "users": [dict(r) for r in rows], "count": len(rows)}
    finally:
        conn.close()


def toggle_user_access(chat_id: int, allowed: bool) -> dict:
    conn = _connect()
    try:
        conn.execute("UPDATE telegram_users SET allowed = ? WHERE chat_id = ?", (int(allowed), chat_id))
        conn.commit()
        return {"ok": True, "chat_id": chat_id, "allowed": allowed}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# LOG
# ═══════════════════════════════════════════════════════════════

def _log_message(chat_id: int, direction: str, text: str):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO telegram_log (chat_id, direction, text, created_at) VALUES (?,?,?,?)",
            (chat_id, direction, text[:2000], datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_telegram_log(limit: int = 50) -> dict:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM telegram_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"ok": True, "log": [dict(r) for r in rows], "count": len(rows)}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# TELEGRAM API
# ═══════════════════════════════════════════════════════════════

def _tg_request(method: str, token: str, data: dict = None, timeout: int = 60) -> dict:
    """Запрос к Telegram Bot API."""
    url = f"{TG_API.format(token=token)}/{method}"
    try:
        resp = requests.post(url, json=data or {}, timeout=timeout)
        return resp.json()
    except Exception as e:
        logger.error(f"TG API error [{method}]: {e}")
        return {"ok": False, "description": str(e)}


def _send_message(token: str, chat_id: int, text: str, parse_mode: str = "Markdown"):
    """Отправить сообщение в Telegram."""
    max_len = int(_get_config("max_message_length", "4000"))
    # Telegram лимит 4096 символов
    if len(text) > max_len:
        text = text[:max_len] + "\n\n_(сообщение обрезано)_"

    # Если Markdown парсинг может упасть — фоллбэк на plain text
    result = _tg_request("sendMessage", token, {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    })
    if not result.get("ok") and parse_mode:
        # Фоллбэк без парсинга (если markdown сломан)
        result = _tg_request("sendMessage", token, {
            "chat_id": chat_id,
            "text": text,
        })
    return result


def _send_typing(token: str, chat_id: int):
    """Показать 'печатает...' в Telegram."""
    _tg_request("sendChatAction", token, {"chat_id": chat_id, "action": "typing"}, timeout=5)


def test_telegram_connection() -> dict:
    """Проверить подключение к Telegram Bot API."""
    token = _get_config("bot_token", "")
    if not token:
        return {"ok": False, "error": "Токен бота не задан"}
    result = _tg_request("getMe", token, timeout=10)
    if result.get("ok"):
        bot = result["result"]
        return {
            "ok": True,
            "bot_username": bot.get("username", ""),
            "bot_name": bot.get("first_name", ""),
            "bot_id": bot.get("id", 0),
        }
    return {"ok": False, "error": result.get("description", "Неизвестная ошибка")}


# ═══════════════════════════════════════════════════════════════
# ОБРАБОТКА СООБЩЕНИЙ
# ═══════════════════════════════════════════════════════════════

def _process_message(token: str, message: dict):
    """Обрабатывает входящее сообщение."""
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    user = message.get("from", {})
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return

    # Регистрируем пользователя
    _register_user(
        chat_id,
        username=user.get("username", ""),
        first_name=user.get("first_name", ""),
        last_name=user.get("last_name", ""),
    )

    # Проверяем доступ
    if not _is_user_allowed(chat_id):
        _send_message(token, chat_id, "⛔ Доступ ограничен. Обратитесь к администратору.", "")
        return

    # Логируем входящее
    _log_message(chat_id, "in", text)

    # Команды
    if text.startswith("/"):
        _handle_command(token, chat_id, text)
        return

    # Показываем "печатает..."
    _send_typing(token, chat_id)

    # Генерируем ответ через LLM
    try:
        model = _get_config("model", "")
        profile = _get_config("profile", "Универсальный")
        use_memory = _get_config("use_memory", "true") == "true"
        use_web = _get_config("use_web_search", "false") == "true"

        from app.services.agents_service import run_agent
        result = run_agent(
            model_name=model or "gemma3:4b",
            profile_name=profile,
            user_input=text,
            use_memory=use_memory,
            use_web_search=use_web,
        )
        answer = result.get("answer", "Не удалось получить ответ 😔")

        # Убираем HTML-теги и <think> блоки
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()

        # Логируем и отправляем
        _log_message(chat_id, "out", answer)
        _send_message(token, chat_id, answer)

    except Exception as e:
        logger.error(f"TG process error: {e}")
        _send_message(token, chat_id, f"⚠️ Ошибка: {str(e)[:200]}", "")


def _handle_command(token: str, chat_id: int, text: str):
    """Обработка /команд."""
    cmd = text.split()[0].lower().split("@")[0]  # /start@botname → /start

    if cmd == "/start":
        welcome = _get_config("welcome_message", "Привет! Я Elira — твоя AI-ассистентка 🤖✨\nПиши мне что угодно!")
        _send_message(token, chat_id, welcome, "")
        _log_message(chat_id, "cmd", "/start")

    elif cmd == "/help":
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
        _send_message(token, chat_id, help_text)
        _log_message(chat_id, "cmd", "/help")

    elif cmd == "/status":
        model = _get_config("model", "auto")
        profile = _get_config("profile", "Универсальный")
        web = _get_config("use_web_search", "false")
        mem = _get_config("use_memory", "true")
        status = (
            f"📊 *Статус Elira*\n\n"
            f"🧠 Модель: `{model}`\n"
            f"👤 Профиль: {profile}\n"
            f"🌐 Веб-поиск: {'✅' if web == 'true' else '❌'}\n"
            f"💾 Память: {'✅' if mem == 'true' else '❌'}"
        )
        _send_message(token, chat_id, status)

    elif cmd == "/web":
        parts = text.split()
        if len(parts) > 1 and parts[1].lower() in ("on", "off"):
            val = parts[1].lower() == "on"
            _set_config("use_web_search", "true" if val else "false")
            _send_message(token, chat_id, f"🌐 Веб-поиск: {'✅ включён' if val else '❌ выключен'}", "")
        else:
            _send_message(token, chat_id, "Использование: /web on или /web off", "")

    elif cmd == "/memory":
        parts = text.split()
        if len(parts) > 1 and parts[1].lower() in ("on", "off"):
            val = parts[1].lower() == "on"
            _set_config("use_memory", "true" if val else "false")
            _send_message(token, chat_id, f"💾 Память: {'✅ включена' if val else '❌ выключена'}", "")
        else:
            _send_message(token, chat_id, "Использование: /memory on или /memory off", "")

    else:
        _send_message(token, chat_id, "Неизвестная команда. Напиши /help для справки.", "")


# ═══════════════════════════════════════════════════════════════
# POLLING LOOP
# ═══════════════════════════════════════════════════════════════

def _poll_loop():
    """Long-polling цикл получения обновлений."""
    global _running, _last_update_id

    token = _get_config("bot_token", "")
    if not token:
        logger.error("Telegram bot: нет токена")
        _running = False
        return

    logger.info("Telegram bot polling started")

    while _running:
        try:
            result = _tg_request("getUpdates", token, {
                "offset": _last_update_id + 1,
                "timeout": 30,
                "allowed_updates": ["message"],
            }, timeout=35)

            if not result.get("ok"):
                logger.warning(f"TG getUpdates error: {result.get('description', '?')}")
                time.sleep(5)
                continue

            for update in result.get("result", []):
                _last_update_id = update["update_id"]
                msg = update.get("message")
                if msg:
                    try:
                        _process_message(token, msg)
                    except Exception as e:
                        logger.error(f"TG message processing error: {e}")

        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.error(f"TG poll error: {e}")
            time.sleep(5)

    logger.info("Telegram bot polling stopped")


def start_telegram_bot() -> dict:
    """Запустить Telegram-бота."""
    global _running, _bot_thread

    if _running:
        return {"ok": True, "status": "already_running"}

    token = _get_config("bot_token", "")
    if not token:
        return {"ok": False, "error": "Токен бота не задан. Укажите bot_token в настройках."}

    # Проверяем подключение
    test = test_telegram_connection()
    if not test.get("ok"):
        return {"ok": False, "error": f"Не удалось подключиться: {test.get('error', '?')}"}

    _running = True
    _bot_thread = threading.Thread(target=_poll_loop, daemon=True, name="telegram-bot")
    _bot_thread.start()

    return {
        "ok": True,
        "status": "started",
        "bot_username": test.get("bot_username", ""),
        "bot_name": test.get("bot_name", ""),
    }


def stop_telegram_bot() -> dict:
    """Остановить Telegram-бота."""
    global _running, _bot_thread

    _running = False
    if _bot_thread and _bot_thread.is_alive():
        _bot_thread.join(timeout=5)
    _bot_thread = None

    return {"ok": True, "status": "stopped"}


def telegram_bot_status() -> dict:
    """Статус бота."""
    config = get_telegram_config()
    return {
        "ok": True,
        "running": _running,
        "has_token": config.get("has_token", False),
        "bot_token_preview": config.get("bot_token", ""),
    }
