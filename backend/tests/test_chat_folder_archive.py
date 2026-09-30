"""Folder archive export: ZIP of per-chat Markdown transcripts.

Covers the acceptance criteria for «Скачать архив»:
- one .md per chat of the folder, full user + assistant text with author marks
- Windows-safe file names, duplicate titles never overwrite each other
- UTF-8 without BOM, LF line endings, Cyrillic and Markdown preserved
- other folders/chats excluded, source sessions untouched
- clear error responses for missing/empty folders
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes import code_agent_routes as routes  # noqa: E402
from app.application.code_agent import archive, sessions  # noqa: E402


@pytest.fixture(autouse=True)
def restore_session_database(monkeypatch):
    monkeypatch.setattr(sessions, "DB_PATH", sessions.DB_PATH)


def _client(tmp_path: Path) -> TestClient:
    database = tmp_path / "code_agent_sessions.db"
    sessions.DB_PATH = database
    sessions.init_db()
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _seed_folder(client: TestClient, folder_id: str, folder_name: str, chats: list[dict]) -> None:
    """Create the folder, then chats, and assign them through the real store."""
    client.patch(
        "/api/code-agent/chat-folders",
        json={"operation": "create", "folder_id": folder_id, "name": folder_name},
    )
    for chat in chats:
        session = sessions.create_session(title=chat["title"])
        sessions.update_session(
            session["id"],
            {"turns": chat["turns"], "pinned": chat.get("pinned", False)},
        )
        client.patch(
            "/api/code-agent/chat-folders",
            json={"operation": "assign", "session_id": session["id"], "folder_id": folder_id},
        )


def _md_entries(response) -> dict[str, str]:
    """Decode the ZIP body into {entry_name: utf-8 text}."""
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


def test_archive_contains_one_markdown_per_chat_with_authors_and_safe_names(tmp_path) -> None:
    client = _client(tmp_path)
    folder_id = "folder-aurora"
    user_text = "Задача: строка 0.\n\n- пункт А\n- пункт Б\n"
    answer_text = "Расчёт выполнен: 100,125.\n```text\nисходный текст\n```\n"
    chats = [
        {"title": "Повторяющийся заголовок", "pinned": True,
         "turns": [{"kind": "user", "id": "u1", "text": user_text},
                   {"kind": "agent", "id": "a1", "text": answer_text, "toolCalls": []}]},
        {"title": "Повторяющийся заголовок",
         "turns": [{"kind": "user", "id": "u2", "text": "Второй чат, тот же заголовок."},
                   {"kind": "agent", "id": "a2", "text": "Ответ второго чата.", "toolCalls": []}]},
        {"title": "Ёж и север: план/факт?",
         "turns": [{"kind": "user", "id": "u3", "text": "Ёжик на севере."},
                   {"kind": "agent", "id": "a3", "text": "Снег и хвоя.", "toolCalls": []}]},
    ]
    _seed_folder(client, folder_id, "Аврора — неделя 1", chats)
    before = [sessions.get_session(s["id"]) for s in sessions.list_sessions()]

    response = client.get(f"/api/code-agent/chat-folders/{folder_id}/archive")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    # RFC 5987: the Cyrillic name is carried in filename*=UTF-8''…
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=")
    assert "filename*=UTF-8''" in disposition
    from urllib.parse import unquote
    assert unquote(disposition.split("filename*=UTF-8''", 1)[1]) == "Аврора — неделя 1.zip"
    assert response.headers.get("x-chat-count") == "3"

    entries = _md_entries(response)
    names = sorted(entries)
    # Duplicate titles must not overwrite each other: both files exist.
    assert names == [
        "Ёж и север_ план_факт_.md",
        "Повторяющийся заголовок (2).md",
        "Повторяющийся заголовок.md",
    ], names
    # Forbidden Windows characters are replaced, not dropped from the archive.
    assert all(ch not in name for name in names for ch in '<>:"/\\|?*')

    first = entries["Повторяющийся заголовок.md"]
    assert first.startswith("# Повторяющийся заголовок\n")
    assert "## 👤 Пользователь" in first and "## 🤖 Elira" in first
    assert user_text in first and answer_text in first
    # Author order follows the conversation: user before assistant.
    assert first.index("## 👤 Пользователь") < first.index("## 🤖 Elira")
    # Markdown and Cyrillic are preserved verbatim.
    assert "```text\nисходный текст\n```" in first
    assert "Ёжик на севере." in entries["Ёж и север_ план_факт_.md"]

    # Encoding: UTF-8 without BOM, LF line endings in every entry.
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        for name in zf.namelist():
            raw = zf.read(name)
            assert not raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {name}"
            assert b"\r" not in raw, f"CRLF/CR found in {name}"
    assert "\r" not in first

    # Source sessions are not modified by the export.
    after = [sessions.get_session(s["id"]) for s in sessions.list_sessions()]
    assert after == before
    assert client.get("/api/code-agent/chat-folders").json()["state"]["assign"] == {
        item["id"]: folder_id for item in before
    }


def test_archive_excludes_other_folders_and_chats(tmp_path) -> None:
    client = _client(tmp_path)
    inside = sessions.create_session(title="Внутри папки")
    sessions.update_session(inside["id"], {"turns": [{"kind": "user", "id": "u", "text": "внутри"}]})
    outside = sessions.create_session(title="НЕ ВКЛЮЧАТЬ")
    sessions.update_session(outside["id"], {"turns": [{"kind": "user", "id": "u", "text": "снаружи"}]})
    other = sessions.create_session(title="Другая папка")
    sessions.update_session(other["id"], {"turns": [{"kind": "user", "id": "u", "text": "другое"}]})
    client.patch("/api/code-agent/chat-folders", json={"operation": "create", "folder_id": "f1", "name": "Первая"})
    client.patch("/api/code-agent/chat-folders", json={"operation": "create", "folder_id": "f2", "name": "Вторая"})
    client.patch("/api/code-agent/chat-folders", json={"operation": "assign", "session_id": inside["id"], "folder_id": "f1"})
    client.patch("/api/code-agent/chat-folders", json={"operation": "assign", "session_id": other["id"], "folder_id": "f2"})

    response = client.get("/api/code-agent/chat-folders/f1/archive")
    assert response.status_code == 200
    entries = _md_entries(response)
    assert list(entries) == ["Внутри папки.md"]
    assert "снаружи" not in entries["Внутри папки.md"]
    assert "другое" not in entries["Внутри папки.md"]


def test_archive_errors_are_clear_and_stable(tmp_path) -> None:
    client = _client(tmp_path)
    missing = client.get("/api/code-agent/chat-folders/nope/archive")
    assert missing.status_code == 404
    assert "Папка не найдена" in missing.json()["detail"]

    client.patch("/api/code-agent/chat-folders", json={"operation": "create", "folder_id": "empty", "name": "Пустая"})
    empty = client.get("/api/code-agent/chat-folders/empty/archive")
    assert empty.status_code == 409
    assert "нет чатов" in empty.json()["detail"]


def test_sanitize_windows_name_covers_forbidden_and_edge_cases() -> None:
    assert archive.sanitize_windows_name('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert archive.sanitize_windows_name("   ") == "Без названия"
    assert archive.sanitize_windows_name("...") == "Без названия"
    assert archive.sanitize_windows_name("Ёж и север") == "Ёж и север"
    long_name = archive.sanitize_windows_name("x" * 300)
    assert len(long_name) <= 100
    # Control characters are replaced too.
    assert "\x00" not in archive.sanitize_windows_name("a\x00b")


def test_session_markdown_handles_missing_and_files_turns() -> None:
    text = archive.session_markdown({
        "id": "s-1", "title": "Тест", "created_at": 1790715623773, "updated_at": 1790715623773,
        "turns": [
            {"kind": "user", "id": "u", "text": "вопрос"},
            {"kind": "files", "id": "f", "files": [{"name": "отчёт.xlsx"}]},
            {"kind": "agent", "id": "a", "text": "ответ\r\nс CRLF", "toolCalls": []},
            {"kind": "unknown", "id": "x"},
            {"id": "no-kind"},
        ],
    })
    assert "## 📎 Файлы" in text and "отчёт.xlsx" in text
    assert "ответ\nс CRLF" in text and "\r" not in text
    assert text.endswith("\n") and not text.startswith("\ufeff")


def test_build_folder_archive_returns_bytes_and_name(tmp_path) -> None:
    client = _client(tmp_path)
    chat = sessions.create_session(title="Чат")
    sessions.update_session(chat["id"], {"turns": [{"kind": "user", "id": "u", "text": "привет"}]})
    client.patch("/api/code-agent/chat-folders", json={"operation": "create", "folder_id": "f", "name": "Неделя"})
    client.patch("/api/code-agent/chat-folders", json={"operation": "assign", "session_id": chat["id"], "folder_id": "f"})

    data, name, count = archive.build_folder_archive("f")
    assert name == "Неделя.zip" and count == 1
    assert data[:2] == b"PK"


def test_archive_names_survive_windows_extraction_without_collisions(tmp_path) -> None:
    client = _client(tmp_path)
    titles = ["CON", "nul.txt", "COM1", "LPT9.log", "Чат", "чат", "ЧАТ", " . . "]
    _seed_folder(client, "portable", "CON", [
        {"title": title, "turns": [{"kind": "user", "text": f"Сообщение {index}"}]}
        for index, title in enumerate(titles)
    ])

    data, filename, count = archive.build_folder_archive("portable")
    assert filename == "_CON.zip" and count == len(titles)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert len({name.casefold() for name in names}) == len(titles)
        assert {"_CON.md", "_nul.txt.md", "_COM1.md", "_LPT9.log.md"} <= set(names)
        assert "Без названия.md" in names
        for index in range(len(titles)):
            assert sum(f"Сообщение {index}" in zf.read(name).decode("utf-8") for name in names) == 1


def test_transcript_preserves_final_message_whitespace_and_normalizes_metadata() -> None:
    message = "Ответ с отступом  \n\n"
    text = archive.session_markdown({"id": "whitespace", "title": "Название\r\nчата",
                                     "turns": [{"kind": "agent", "text": message}]})
    assert message in text
    assert "\r" not in text
