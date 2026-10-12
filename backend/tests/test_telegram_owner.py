"""The Telegram owner (John 2026-10-12): bound by a one-time code, talks to Elira as in the app."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.skill_services.telegram_runtime as tg_rt  # noqa: E402
import app.application.telegram.store as tg_store  # noqa: E402
from app.application.code_agent import sessions  # noqa: E402

OWNER, STRANGER = 111, 222


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setattr(tg_store, "DB_PATH", tmp_path / "integrations.db")
    tg_store.init_telegram_db()
    tg_store.set_config_value("allowed_users", "whitelist")
    store = {}

    def create_session(**kwargs):
        store["s1"] = {"id": "s1", "title": kwargs.get("title"), "turns": []}
        return dict(store["s1"])

    def update_session(session_id, patch_data):
        store[session_id] = {**store[session_id], **patch_data}
        return store[session_id]
    monkeypatch.setattr(sessions, "create_session", create_session)
    monkeypatch.setattr(sessions, "get_session", lambda session_id: store.get(session_id))
    monkeypatch.setattr(sessions, "update_session", update_session)
    sent = []
    monkeypatch.setattr(tg_rt, "send_message", lambda token, chat_id, text, parse_mode="Markdown":
                        sent.append((chat_id, text)) or {"ok": True})
    monkeypatch.setattr(tg_rt, "send_typing", lambda token, chat_id: None)
    requests_made = []

    def tg_request(method, token, data=None, *, timeout=60):
        requests_made.append((method, dict(data or {})))
        return {"ok": True, "result": {"message_id": 9}}
    monkeypatch.setattr(tg_rt, "tg_request", tg_request)
    return {"sent": sent, "sessions": store, "requests": requests_made}


def _message(chat_id: int, text: str) -> dict:
    return {"chat": {"id": chat_id}, "from": {"username": f"u{chat_id}"}, "text": text}


def test_a_stranger_is_refused_and_pairs_only_with_the_shown_code(bot):
    tg_rt.process_message("t", _message(STRANGER, "привет"))
    assert bot["sent"][-1] == (STRANGER, "⛔ Доступ ограничен. Обратитесь к администратору.")
    code = tg_rt.create_pair_code()["code"]
    tg_rt.process_message("t", _message(STRANGER, "/pair 000000" if code != "000000" else "/pair 111111"))
    assert "не подошёл" in bot["sent"][-1][1] and tg_rt.owner_chat_id() is None
    tg_rt.process_message("t", _message(OWNER, f"/pair {code}"))
    assert tg_rt.owner_chat_id() == OWNER and "владелец" in bot["sent"][-1][1]
    tg_rt.process_message("t", _message(STRANGER, f"/pair {code}"))  # one binding per code
    assert tg_rt.owner_chat_id() == OWNER


def test_five_wrong_codes_burn_the_code(bot):
    code = tg_rt.create_pair_code()["code"]
    wrong = "123456" if code != "123456" else "654321"
    for _ in range(5):
        tg_rt.process_message("t", _message(STRANGER, f"/pair {wrong}"))
    tg_rt.process_message("t", _message(OWNER, f"/pair {code}"))
    assert tg_rt.owner_chat_id() is None


def test_the_owner_talks_to_the_same_agent_with_history_and_full_rights(bot):
    tg_store.set_config_value("owner_chat_id", str(OWNER))
    calls = []

    def run_agent(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "answer": "Ответ " + "длинный абзац. " * 400,
                "tool_results": [{"tool": "run_bash", "ok": True, "result": "done", "arguments": {"command": "ver"}}]}
    with patch("app.application.chat.runtime.run_agent", run_agent):
        tg_rt.process_message("t", _message(OWNER, "первый вопрос"))
        tg_rt.process_message("t", _message(OWNER, "второй вопрос"))
    assert calls[0]["permission_mode"] == "bypass" and calls[0]["history"] == []
    assert calls[0]["reasoning_effort"] == "low"  # John: Telegram always thinks «Коротко»
    assert [item["role"] for item in calls[1]["history"]] == ["user", "assistant"]
    assert calls[1]["history"][0]["content"] == "первый вопрос"
    turns = bot["sessions"]["s1"]["turns"]
    assert [turn["kind"] for turn in turns] == ["user", "agent", "user", "agent"]
    assert turns[1]["toolCalls"][0]["tool"] == "run_bash" and turns[0]["source"] == "telegram"
    owner_messages = [text for chat_id, text in bot["sent"] if chat_id == OWNER]
    assert len(owner_messages) >= 4 and all(len(text) <= 4096 for text in owner_messages)  # split, not cut


def test_only_the_owner_changes_the_bot_settings(bot):
    tg_store.set_config_value("owner_chat_id", str(OWNER))
    tg_store.register_user(STRANGER, username="guest")
    tg_store.toggle_user_access(STRANGER, True)
    tg_rt.process_message("t", _message(STRANGER, "/web on"))
    assert bot["sent"][-1] == (STRANGER, "Эту настройку меняет только владелец.")
    assert tg_store.get_config_value("use_web_search", "false") == "false"


def test_the_bot_left_on_starts_when_the_token_can_be_read(bot, monkeypatch):
    started = []
    monkeypatch.setattr(tg_rt, "start_telegram_bot", lambda: started.append(1) or {"ok": True})
    assert tg_rt.autostart_telegram_bot()["status"] == "skipped" and not started  # never switched on
    tg_store.set_config_value("enabled", "true")
    tg_rt.autostart_telegram_bot()
    assert started == [1]


def test_the_plain_copy_of_the_token_goes_once_the_vault_holds_it(bot, monkeypatch):
    from app.infrastructure.secrets import vault

    tg_store.set_config_value("bot_token", "123:plain")
    tg_store.set_config_value("bot_token_ref", "sref_x")
    monkeypatch.setattr(vault, "resolve", lambda ref: "123:from-vault")
    assert tg_rt._telegram_token() == "123:from-vault"
    assert tg_store.get_config_value("bot_token", "") == ""
    monkeypatch.setattr(vault, "resolve", lambda ref: (_ for _ in ()).throw(vault.VaultLocked("portable vault is locked")))
    assert "Хранилище секретов заперто" in tg_rt.test_telegram_connection()["error"]


def test_a_short_status_line_follows_the_run_and_goes_with_the_answer(bot):
    # John 2026-10-12: the app's few words ("Думает…", "Выполняю команду") in Telegram too.
    import time as clock

    tg_store.set_config_value("owner_chat_id", str(OWNER))

    def run_agent(**kwargs):
        on_event = kwargs["on_event"]
        on_event({"type": "step_started"})
        clock.sleep(1.3)
        on_event({"type": "tool_started", "tool": "run_bash"})
        clock.sleep(1.3)
        on_event({"type": "delta", "text": "Ответ"})
        clock.sleep(1.3)
        return {"ok": True, "answer": "Ответ", "tool_results": []}
    with patch("app.application.chat.runtime.run_agent", run_agent):
        tg_rt.process_message("t", _message(OWNER, "проверь"))
    methods = [method for method, _ in bot["requests"]]
    assert methods[0] == "sendMessage" and bot["requests"][0][1]["text"] == "🤔 Думаю…"
    edits = [data["text"] for method, data in bot["requests"] if method == "editMessageText"]
    assert edits == ["💻 Выполняю команду", "✍️ Пишу ответ…"]
    assert methods[-1] == "deleteMessage" and bot["sent"][-1] == (OWNER, "Ответ")


def test_a_ticked_user_talks_without_actions(bot):
    tg_store.set_config_value("owner_chat_id", str(OWNER))
    tg_store.register_user(STRANGER, username="guest")
    tg_store.toggle_user_access(STRANGER, True)
    calls = []
    with patch("app.application.chat.runtime.run_agent",
               lambda **kwargs: calls.append(kwargs) or {"ok": True, "answer": "привет", "tool_results": []}):
        tg_rt.process_message("t", _message(STRANGER, "привет"))
    assert calls[0]["read_only"] is True and calls[0].get("permission_mode", "ask") != "bypass"
    assert calls[0]["reasoning_effort"] == "low" and "session_id" not in calls[0]  # not the owner's chat
