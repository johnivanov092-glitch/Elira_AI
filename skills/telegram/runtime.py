from __future__ import annotations

import hmac
import logging
import re
import threading
import time
import uuid
from typing import Any

import requests

from app.application.telegram.store import (
    get_config_value,
    log_message,
    register_user,
    is_user_allowed,
    set_config_value,
    toggle_user_access,
    owner_chat_id,
    create_pair_code,
)


logger = logging.getLogger(__name__)

TG_API = "https://api.telegram.org/bot{token}"
DEFAULT_PROFILE = "Универсальный"
DEFAULT_WELCOME_MESSAGE = "Привет! Я Elira — твоя AI-ассистентка 🤖✨\nПиши мне что угодно!"

_bot_thread: threading.Thread | None = None
_running = False
_last_update_id = 0


def _telegram_token() -> str:
    """Resolve the bot token only inside runtime; config stores an opaque ref."""
    secret_ref = get_config_value("bot_token_ref", "").strip()
    if secret_ref:
        from app.infrastructure.secrets import vault

        if get_config_value("bot_token", ""):
            # John 2026-10-12: the token lives in Elira's vault only; the old
            # plain-text copy goes once a vault ref exists.
            set_config_value("bot_token", "")
        return vault.resolve(secret_ref)
    # Read-only compatibility for installations not migrated yet. No new API/UI
    # writes this key.
    return get_config_value("bot_token", "")


def _token_error(exc: Exception) -> str:
    """The person's words for why the token cannot be read."""
    if "locked" in str(exc).lower():
        return ("Хранилище секретов заперто: разблокируйте его (Настройки → Telegram или «Хранилище "
                "секретов») — включённый бот запустится сам.")
    return f"Не удалось прочитать токен бота: {exc}"


def get_telegram_config() -> dict[str, Any]:
    secret_ref = get_config_value("bot_token_ref", "").strip()
    legacy_token = get_config_value("bot_token", "")
    return {
        "ok": True,
        "bot_token": "vault:" + secret_ref[-8:] if secret_ref else ("legacy:***" if legacy_token else ""),
        "bot_token_ref": secret_ref,
        "has_token": bool(secret_ref or legacy_token),
        "legacy_token_present": bool(legacy_token),
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


def send_telegram_message(
    *,
    chat_id: int,
    text: str,
    parse_mode: str = "Markdown",
) -> dict[str, Any]:
    """Send through the configured bot without exposing its token to the agent."""
    message = str(text or "").strip()
    if not message:
        raise ValueError("Текст Telegram-сообщения не задан")
    token = _telegram_token()
    if not token:
        raise ValueError("Токен Telegram-бота не настроен")

    response = send_message(token, int(chat_id), message, parse_mode=parse_mode)
    if not response.get("ok"):
        return {
            "ok": False,
            "error": str(response.get("description") or "Telegram sendMessage failed"),
        }

    payload = response.get("result") if isinstance(response.get("result"), dict) else {}
    log_message(int(chat_id), "out", message)
    return {
        "ok": True,
        "chat_id": int(chat_id),
        "message_id": payload.get("message_id"),
        "date": payload.get("date"),
    }


def send_typing(token: str, chat_id: int) -> None:
    tg_request(
        "sendChatAction",
        token,
        {"chat_id": chat_id, "action": "typing"},
        timeout=5,
    )


def test_telegram_connection() -> dict[str, Any]:
    try:
        token = _telegram_token()
    except Exception as exc:
        return {"ok": False, "error": _token_error(exc)}
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


# -- the owner (John 2026-10-12) ------------------------------------------------
# The owner talks to Elira as in the app: the same agent, full rights (bypass),
# one "Telegram" chat with its history, shared with the app's chat list. The
# owner is bound by a one-time code shown only in Settings → Telegram.
_PAIR_ATTEMPTS = 5
_HISTORY_TURNS = 20
_CHUNK = 3900
_REASONING = "low"


def _pair(token: str, chat_id: int, text: str) -> None:
    parts = text.split()
    code = parts[1].strip() if len(parts) > 1 else ""
    expected = get_config_value("pair_code", "")
    try:
        expires = float(get_config_value("pair_expires", "0") or 0)
    except ValueError:
        expires = 0.0
    if expected and time.time() < expires and code and hmac.compare_digest(code, expected):
        set_config_value("owner_chat_id", str(chat_id))
        set_config_value("pair_code", "")
        toggle_user_access(chat_id, True)
        log_message(chat_id, "cmd", "/pair ok")
        send_message(token, chat_id, "✅ Готово: вы владелец Elira. Пишите как в обычном чате — полный доступ, "
                                     "переписка видна в Elira в чате «Telegram».", "")
        return
    attempts = int(get_config_value("pair_attempts", "0") or 0) + 1
    set_config_value("pair_attempts", str(attempts))
    if attempts >= _PAIR_ATTEMPTS:
        set_config_value("pair_code", "")  # a guessed code must not stay valid
    log_message(chat_id, "cmd", "/pair failed")
    send_message(token, chat_id, "Код не подошёл или устарел. Получите новый: Elira → Настройки → Telegram.", "")


def _telegram_session() -> dict[str, Any]:
    from app.application.code_agent import sessions

    session_id = get_config_value("session_id", "")
    session = sessions.get_session(session_id) if session_id else None
    if session is None:
        session = sessions.create_session(title="Telegram", project_root="", model="auto")
        set_config_value("session_id", session["id"])
    return session


def _chunks(text: str) -> list[str]:
    """Telegram takes up to 4096 characters: paragraphs stay whole where they fit."""
    chunks, current = [], ""
    for paragraph in text.split("\n\n"):
        while len(paragraph) > _CHUNK:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:_CHUNK])
            paragraph = paragraph[_CHUNK:]
        if current and len(current) + 2 + len(paragraph) > _CHUNK:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    return chunks or [""]


# The app's short live line (ToolCallGroup ACTIVE_LABELS, «Думает…»): John
# 2026-10-12 wants the same few words in Telegram while Elira works.
_STATUS_LABELS = {
    "read_file": "📄 Читаю файл", "write_file": "✏️ Записываю файл", "edit_file": "✏️ Изменяю файл",
    "glob": "🔎 Ищу файлы", "grep": "🔎 Ищу в проекте", "run_bash": "💻 Выполняю команду",
    "run_server": "💻 Запускаю процесс", "web_search": "🌐 Ищу в интернете", "web_fetch": "🌐 Читаю страницу",
    "memory": "🧠 Работаю с памятью", "recall": "🔎 Ищу по проекту", "library": "📚 Читаю Библиотеку",
    "math": "🧮 Считаю", "mcp": "🔌 Работаю с MCP", "resource_publish": "📦 Готовлю файл",
    "resource_process": "📦 Обрабатываю файл", "todo_update": "📝 Обновляю план",
    "delegate_task": "🤝 Передаю задачу", "capability_load": "🔌 Подключаю инструменты",
}
_THINKING, _WRITING = "🤔 Думаю…", "✍️ Пишу ответ…"


class _StatusLine:
    """One short message edited as the run goes; removed when the answer comes."""

    def __init__(self, token: str, chat_id: int) -> None:
        self.token, self.chat_id = token, chat_id
        self.wanted = self.shown = _THINKING
        self.done = threading.Event()
        sent = tg_request("sendMessage", token, {"chat_id": chat_id, "text": _THINKING}, timeout=10)
        self.message_id = (sent.get("result") or {}).get("message_id") if sent.get("ok") else None
        threading.Thread(target=self._loop, daemon=True, name="telegram-status").start()

    def event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "tool_started":
            tool = str(event.get("tool") or "")
            self.wanted = (_STATUS_LABELS.get(tool) or ("🌐 Работаю в браузере" if tool.startswith("playwright__")
                           else "🔌 Работаю с MCP" if "__" in tool else "⚙️ Работаю"))
        elif kind in ("step_started", "reasoning_delta"):
            self.wanted = _THINKING
        elif kind == "delta" and str(event.get("text") or "").strip():
            self.wanted = _WRITING

    def _loop(self) -> None:
        ticks = 0
        while not self.done.wait(1.0):
            ticks += 1
            if self.message_id and self.wanted != self.shown:
                wanted = self.wanted
                tg_request("editMessageText", self.token, {"chat_id": self.chat_id, "message_id": self.message_id,
                                                            "text": wanted}, timeout=10)
                self.shown = wanted
            if ticks % 5 == 0:
                send_typing(self.token, self.chat_id)

    def close(self) -> None:
        self.done.set()
        if self.message_id:
            tg_request("deleteMessage", self.token, {"chat_id": self.chat_id, "message_id": self.message_id}, timeout=10)


def _owner_turn(token: str, chat_id: int, text: str) -> None:
    from app.application.chat.runtime import run_agent
    from app.application.code_agent import sessions

    session = _telegram_session()
    session_id = session["id"]
    turns = list(session.get("turns") or [])
    history = [{"role": "user" if turn.get("kind") == "user" else "assistant", "content": str(turn.get("text") or "")}
               for turn in turns[-_HISTORY_TURNS:] if turn.get("kind") in ("user", "agent") and turn.get("text")]
    sessions.update_session(session_id, {"turns": [*turns, {
        "kind": "user", "id": f"tg-u-{uuid.uuid4().hex[:10]}", "text": text, "source": "telegram"}]})

    send_typing(token, chat_id)
    status = _StatusLine(token, chat_id)
    try:
        result = run_agent(model_name=get_config_value("model", "") or "auto",
                           profile_name=get_config_value("profile", DEFAULT_PROFILE), user_input=text,
                           memory_query=text, session_id=session_id, agent_id="telegram", history=history,
                           permission_mode="bypass", use_memory=get_config_value("use_memory", "true") == "true",
                           # John 2026-10-12: in Telegram Elira always thinks "low" («Коротко»), as the app's chat chip.
                           reasoning_effort=_REASONING, on_event=status.event)
    except Exception as exc:
        logger.error("TG owner run failed: %s", exc)
        result = {"ok": False, "answer": f"⚠️ Ошибка: {str(exc)[:300]}", "tool_results": []}
    finally:
        status.close()
    answer = re.sub(r"<think>.*?</think>", "", str(result.get("answer") or ""), flags=re.DOTALL).strip()
    answer = answer or "Не удалось получить ответ."
    calls = [{"step": index, "tool": call.get("tool"), "arguments": call.get("arguments") or {},
              "result": str(call.get("result") or "")[:4000], "ok": bool(call.get("ok"))}
             for index, call in enumerate(result.get("tool_results") or [], 1)]
    latest = sessions.get_session(session_id) or {"turns": []}  # the app may have added turns meanwhile
    sessions.update_session(session_id, {"turns": [*(latest.get("turns") or []), {
        "kind": "agent", "id": f"tg-a-{uuid.uuid4().hex[:10]}", "text": answer, "toolCalls": calls,
        "running": False, "answerStatus": "complete" if result.get("ok") else "degraded", "source": "telegram"}]})
    log_message(chat_id, "out", answer)
    for chunk in _chunks(answer):
        send_message(token, chat_id, chunk)


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

    # Before the access check: whoever holds the code shown in Settings becomes the owner.
    if text.split()[0].lower().split("@")[0] == "/pair":
        _pair(token, chat_id, text)
        return

    owner = chat_id == owner_chat_id()
    if not owner and not is_user_allowed(chat_id):
        send_message(token, chat_id, "⛔ Доступ ограничен. Обратитесь к администратору.", "")
        return

    log_message(chat_id, "in", text)

    if text.startswith("/"):
        handle_command(token, chat_id, text, owner=owner)
        return

    if owner:
        _owner_turn(token, chat_id, text)
        return

    send_typing(token, chat_id)

    try:
        model = get_config_value("model", "")
        profile = get_config_value("profile", DEFAULT_PROFILE)
        use_memory = get_config_value("use_memory", "true") == "true"
        use_web = get_config_value("use_web_search", "false") == "true"

        from app.application.chat.runtime import run_agent

        result = run_agent(
            # P9.3: "auto" sentinel lets run_agent's shared profile routing engage;
            # an explicit configured model is preserved.
            model_name=model or "auto",
            profile_name=profile,
            user_input=text,
            memory_query=text if use_memory else "",
            use_memory=use_memory,
            use_web_search=use_web,
            agent_id="telegram",
            # John 2026-10-12: a ticked user gets a talk, not actions — read-only
            # tools only, so no change can wait for an approval nobody can give.
            read_only=True,
            reasoning_effort=_REASONING,
        )
        answer = result.get("answer", "Не удалось получить ответ 😔")
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()

        log_message(chat_id, "out", answer)
        send_message(token, chat_id, answer)
    except Exception as exc:
        logger.error("TG process error: %s", exc)
        send_message(token, chat_id, f"⚠️ Ошибка: {str(exc)[:200]}", "")


def handle_command(token: str, chat_id: int, text: str, *, owner: bool = False) -> None:
    cmd = text.split()[0].lower().split("@")[0]

    if cmd in ("/web", "/memory") and not owner:
        # They change the bot for everyone: the owner's settings, not a user's.
        send_message(token, chat_id, "Эту настройку меняет только владелец.", "")
        return

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

    try:
        token = _telegram_token()
    except Exception as exc:
        logger.error("Telegram bot token unavailable: %s", exc)
        _running = False
        return
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
    if _bot_thread and _bot_thread.is_alive():
        return {"ok": False, "status": "stopping", "error": "Previous Telegram receiver is still stopping"}

    try:
        token = _telegram_token()
    except Exception as exc:
        return {"ok": False, "error": _token_error(exc)}
    if not token:
        return {
            "ok": False,
            "error": "Токен бота не задан: сохраните его в Настройки → Telegram.",
        }

    test = test_telegram_connection()
    if not test.get("ok"):
        return {
            "ok": False,
            "error": f"Не удалось подключиться: {test.get('error', '?')}",
        }

    set_config_value("enabled", "true")  # comes back by itself after a restart + vault unlock
    _running = True
    _bot_thread = threading.Thread(target=poll_loop, daemon=True, name="telegram-bot")
    _bot_thread.start()

    return {
        "ok": True,
        "status": "started",
        "bot_username": test.get("bot_username", ""),
        "bot_name": test.get("bot_name", ""),
    }


def autostart_telegram_bot() -> dict[str, Any]:
    """Start the bot the person left on (Elira start, vault unlock); quiet when it cannot."""
    if get_config_value("enabled", "") != "true" or _running:
        return {"ok": True, "status": "skipped"}
    result = start_telegram_bot()
    if not result.get("ok"):
        logger.info("Telegram bot autostart waits: %s", result.get("error"))
    return result


def stop_telegram_bot() -> dict[str, Any]:
    global _running, _bot_thread

    set_config_value("enabled", "false")
    _running = False
    if _bot_thread and _bot_thread.is_alive():
        _bot_thread.join(timeout=5)
        if _bot_thread.is_alive():
            return {"ok": True, "status": "stopping"}
    _bot_thread = None

    return {"ok": True, "status": "stopped"}


def telegram_bot_status() -> dict[str, Any]:
    config = get_telegram_config()
    return {
        "ok": True,
        "running": _running,
        "stopping": not _running and _bot_thread is not None and _bot_thread.is_alive(),
        "has_token": config.get("has_token", False),
        "bot_token_preview": config.get("bot_token", ""),
    }
