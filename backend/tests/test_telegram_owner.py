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
    monkeypatch.setattr(tg_rt, "_spawn", lambda target: target())  # the turn runs inline here
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


# ── /stop, voice, photos and files (John 2026-10-12) ──────────────────────────

def _media(chat_id: int, **fields) -> dict:
    return {"chat": {"id": chat_id}, "from": {"username": f"u{chat_id}"}, **fields}


def test_stop_cancels_the_running_task_and_a_second_message_waits(bot, monkeypatch):
    from app.application.code_agent import run_control

    tg_store.set_config_value("owner_chat_id", str(OWNER))
    cancelled = []
    monkeypatch.setattr(run_control, "request_cancel", lambda run_id: cancelled.append(run_id) or True)
    tg_rt.process_message("t", _message(OWNER, "/stop"))
    assert bot["sent"][-1] == (OWNER, "Сейчас ничего не выполняется.") and not cancelled
    entry = {"run_id": "run-1", "stopped": False}
    tg_rt._active[OWNER] = entry
    try:
        tg_rt.process_message("t", _message(OWNER, "ещё вопрос"))
        assert "Ещё работаю" in bot["sent"][-1][1]
        tg_rt.process_message("t", _message(OWNER, "/stop"))
        assert cancelled == ["run-1"] and entry["stopped"] and "Останавливаю" in bot["sent"][-1][1]
    finally:
        tg_rt._active.pop(OWNER, None)


def test_a_finished_or_failed_turn_frees_the_chat(bot):
    tg_store.set_config_value("owner_chat_id", str(OWNER))
    with patch("app.application.chat.runtime.run_agent", lambda **kwargs: {
            "ok": False, "answer": "", "tool_results": [], "meta": {"stop_reason": "cancelled"}}):
        tg_rt.process_message("t", _message(OWNER, "долгая задача"))
    assert OWNER not in tg_rt._active
    assert bot["sent"][-1] == (OWNER, "⏹ Задача остановлена.")


def test_the_owners_voice_note_is_the_message_text(bot, monkeypatch):
    tg_store.set_config_value("owner_chat_id", str(OWNER))
    monkeypatch.setattr(tg_rt, "_download", lambda token, file_id: b"OggS")
    heard = []
    monkeypatch.setattr(tg_rt, "_transcribe", lambda data, suffix: heard.append(suffix) or "проверь свободное место")
    calls = []
    with patch("app.application.chat.runtime.run_agent",
               lambda **kwargs: calls.append(kwargs) or {"ok": True, "answer": "Свободно 120 ГБ.", "tool_results": []}):
        tg_rt.process_message("t", _media(OWNER, voice={"file_id": "v1", "duration": 3}))
    assert heard == [".ogg"]
    assert calls[0]["user_input"] == "проверь свободное место" and calls[0]["resource_refs"] is None
    assert bot["requests"][0][1]["text"] == "🎤 Слушаю голосовое…"
    turns = bot["sessions"]["s1"]["turns"]
    assert turns[0]["text"] == "🎤 проверь свободное место" and turns[1]["text"] == "Свободно 120 ГБ."


def test_the_owners_photo_is_attached_like_the_apps_plus_and_stays_in_history(bot, monkeypatch):
    tg_store.set_config_value("owner_chat_id", str(OWNER))
    monkeypatch.setattr(tg_rt, "_download", lambda token, file_id: b"\xff\xd8\xff jpeg bytes")
    calls = []
    with patch("app.application.chat.runtime.run_agent",
               lambda **kwargs: calls.append(kwargs) or {"ok": True, "answer": "На фото роутер.", "tool_results": []}):
        tg_rt.process_message("t", _media(OWNER, caption="что это?", photo=[
            {"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 20}]))
        tg_rt.process_message("t", _message(OWNER, "а модель какая?"))
    first, second = calls
    ref = first["resource_refs"][0]
    assert ref["kind"] == "image" and ref["name"].startswith("photo_")
    assert "resource_process" in first["base_tools"]
    assert first["user_input"].startswith("что это?") and "[Прикреплённые ресурсы этого запроса" in first["user_input"]
    assert first["memory_query"] == "что это?"
    turns = bot["sessions"]["s1"]["turns"]
    assert turns[0]["resources"] == [{"resource_id": ref["resource_id"]}] and turns[0]["text"] == "что это?"
    # The next message keeps the photo, as the app's history does.
    assert "этого сообщения из истории" in second["history"][0]["content"]
    assert second["resource_refs"][0]["resource_id"] == ref["resource_id"]
    assert second["user_input"] == "а модель какая?"


def test_files_only_from_the_owner_and_not_over_twenty_megabytes(bot, monkeypatch):
    tg_store.set_config_value("owner_chat_id", str(OWNER))
    tg_store.register_user(STRANGER, username="guest")
    tg_store.toggle_user_access(STRANGER, True)
    monkeypatch.setattr(tg_rt, "_download", lambda token, file_id: pytest.fail("must not download"))
    with patch("app.application.chat.runtime.run_agent", lambda **kwargs: pytest.fail("must not run")):
        tg_rt.process_message("t", _media(STRANGER, document={"file_id": "d", "file_name": "a.pdf"}))
        assert "только от владельца" in bot["sent"][-1][1]
        tg_rt.process_message("t", _media(OWNER, document={"file_id": "d", "file_name": "big.iso",
                                                            "file_size": 30 * 1024 * 1024}))
        assert "больше 20 МБ" in bot["sent"][-1][1]


def test_transcription_gives_plain_words_without_timecodes(monkeypatch):
    import subprocess

    def run(argv, **kwargs):
        out = Path(argv[argv.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "voice.txt").write_text(
            "Расшифровка аудио: voice.ogg\nДлительность: ~0.1 мин (4 с).\nТаймкоды: [HH:MM:SS]\n"
            + "=" * 60 + "\n\n[00:00:00] Привет, Элира.\n[00:00:02] Проверь диск.\n", encoding="utf-8")
        assert Path(argv[1]).name == "transcribe.py" and Path(argv[2]).read_bytes() == b"OggS"
        return subprocess.CompletedProcess(argv, 0, "[summary] готово 1 из 1", "")
    monkeypatch.setattr(tg_rt.subprocess, "run", run)
    assert tg_rt._transcribe(b"OggS", ".ogg") == "Привет, Элира. Проверь диск."
