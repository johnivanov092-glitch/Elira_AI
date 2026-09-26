from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3

import pytest

from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.tools import tool_recall, tool_remember, tool_runtime_control
from app.application.code_agent.tools._shell import reset_current_run_id, set_current_run_id
from app.application.memory import facade
from app.application.memory.policy import is_authoritative_fact
from app.application.memory.tool_provenance import tool_memory_provenance
from app.application.smart_memory import store


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "memory.db")
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    store.init_memory_db()
    return tmp_path


@contextmanager
def user_run(root: Path, raw: str, *, run_id: str = "memory-origin"):
    journal = RunJournal(run_id)
    journal.start({"user_message": raw + "\nAttached context is not user testimony.",
                   "memory_query": raw, "project_root": str(root)}, {})
    token = set_current_run_id(run_id)
    try:
        yield
    finally:
        reset_current_run_id(token)
        journal.finish()


def test_model_claim_and_correction_keep_agent_origin_after_reopen(isolated: Path) -> None:
    with user_run(isolated, "Сделай экспорт папки диалогов."):
        created = tool_runtime_control(isolated, operation="memory_add", config={
            "fact": "Экспорт Аврора проверен, все имена Windows безопасны.",
            "source": "user_command", "source_ref": "forged",
        })["result"]
        changed = tool_remember(isolated, fact="Экспорт Аврора ещё не включён.",
                                correction=True, replaces_id=created["id"])
        assert changed["ok"] is True
    # A new connection/init exercises persisted origin, not only a tool mock.
    store.init_memory_db()
    row = store.list_memories(limit=10)["items"][0]
    assert row["text"] == "Экспорт Аврора ещё не включён."
    assert row["source"] == "agent_note"
    assert row["source_ref"] == "run:memory-origin"
    assert not is_authoritative_fact(row)
    assert facade.fact_context("Экспорт Аврора") == ""
    recalled = tool_recall(isolated, query="Экспорт Аврора")["text"]
    assert "source=agent_note" in recalled and "не подтверждённый факт пользователя" in recalled
    assert "run:memory-origin" in recalled


def test_user_literal_and_agent_paraphrase_do_not_merge_or_promote(isolated: Path) -> None:
    fact = "Пользователь предпочитает тёмную тему."
    with user_run(isolated, "Запомни: " + fact):
        tool_remember(isolated, fact=fact)
        # Same text inside an attachment or generated context is not sufficient.
    with user_run(isolated, "Проверь настройки темы.", run_id="memory-observation"):
        note = tool_runtime_control(isolated, operation="memory_add", query=fact)["result"]
        tool_remember(isolated, fact="Attached context is not user testimony.")
    rows = store.list_memories(limit=10)["items"]
    user = next(row for row in rows if row["source"] == "user_command")
    assert user["text"] == fact and is_authoritative_fact(user)
    assert user["importance"] == 8  # Agent repetition did not reinforce it.
    assert note["id"] != user["id"] and note["source"] == "agent_note"
    assert len([row for row in rows if is_authoritative_fact(row)]) == 1
    with user_run(isolated, "Проверь настройки темы.", run_id="unbound-correction"):
        rejected = tool_runtime_control(isolated, operation="memory_add", memory_id=user["id"],
                                        query="Пользователь предпочитает светлую тему.")
    assert rejected["ok"] is False
    untouched = next(row for row in store.list_memories(limit=10)["items"] if row["id"] == user["id"])
    assert untouched["text"] == fact and untouched["source"] == "user_command"
    with user_run(isolated, "Пользователь предпочитает светлую тему.", run_id="memory-correction"):
        corrected = tool_runtime_control(isolated, operation="memory_add", memory_id=user["id"],
                                         query="Пользователь предпочитает светлую тему.")["result"]
    assert corrected["source"] == "user_correction"
    assert corrected["source_ref"] == "run:memory-correction"
    assert corrected["id"] == user["id"]


def test_legacy_schema_migration_preserves_rows_and_manual_correction(isolated: Path) -> None:
    old_db = isolated / "legacy.db"
    with sqlite3.connect(old_db) as conn:
        conn.executescript("""
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'fact', source TEXT NOT NULL DEFAULT 'auto',
                importance INTEGER NOT NULL DEFAULT 5, access_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO memories(text,source) VALUES ('Старое имя пользователя.', 'user');
        """)
    previous = store.DB_PATH
    store.DB_PATH = old_db
    try:
        store.init_memory_db()
        store.init_memory_db()
        row = store.list_memories(limit=10)["items"][0]
        assert row["text"] == "Старое имя пользователя."
        assert row["source"] == "user" and row["source_ref"] == ""
        result = facade.add_fact("Новое имя пользователя.", replaces_id=row["id"])
        assert result["source"] == "user_correction"
        assert result["id"] == row["id"]
    finally:
        store.DB_PATH = previous


def test_internal_delegate_prompt_is_not_user_testimony(isolated: Path) -> None:
    text = "Лолита — жена пользователя."
    legacy = store.add_memory(text, source="runtime_control", importance=7)
    journal = RunJournal("internal-delegate")
    journal.start({"user_message": text, "project_root": str(isolated)}, {})
    token = set_current_run_id("internal-delegate")
    try:
        assert tool_memory_provenance(text) == {"source": "agent_note", "source_ref": "run:internal-delegate"}
        saved = tool_runtime_control(isolated, operation="memory_add", query=text)["result"]
        assert saved["source"] == "agent_note"
        assert saved["id"] != legacy["id"]
        denied = tool_runtime_control(isolated, operation="memory_add", query="Лолита — коллега пользователя.",
                                      memory_id=legacy["id"])
        assert denied["ok"] is False
        rows = {row["id"]: row for row in store.list_memories(limit=10)["items"]}
        assert not is_authoritative_fact(rows[saved["id"]])
        assert rows[legacy["id"]]["text"] == text
        assert rows[legacy["id"]]["importance"] == 7
        assert is_authoritative_fact(rows[legacy["id"]], allow_legacy_runtime_control=True)
    finally:
        reset_current_run_id(token)
        journal.finish()


@pytest.mark.parametrize("operation", ["correction", "dedup"])
@pytest.mark.parametrize("concurrent_change", ["promotion", "delete"])
def test_concurrent_memory_change_cannot_be_overwritten_or_reported_saved(
    isolated: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    concurrent_change: str,
) -> None:
    original_text = "Проект Альфа проверяет складские остатки."
    original = store.add_memory(original_text, source="agent_note", source_ref="run:original")
    interleaved = False

    class InterleavedWriter(sqlite3.Connection):
        def execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
            nonlocal interleaved
            normalized = " ".join(sql.split())
            if not interleaved and normalized.startswith((
                "UPDATE memories SET text =", "UPDATE memories SET importance =",
            )):
                # Selection and its origin check already happened. A separate
                # real SQLite connection now commits the user's change before
                # this writer executes its stale UPDATE.
                interleaved = True
                if concurrent_change == "promotion":
                    promoted = facade.add_fact(
                        original_text, source="manual", source_ref="manual:ui",
                        replaces_id=original["id"], importance=9,
                    )
                    assert promoted["ok"] is True and promoted["source"] == "user_correction"
                else:
                    assert facade.delete_fact(original["id"])["ok"] is True
            return super().execute(sql, parameters)

    def interleaved_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(store.DB_PATH, factory=InterleavedWriter)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(store, "connect_memory_db", interleaved_connection)
    result = store.add_memory(
        "Проект Альфа ожидает проверки остатков." if operation == "correction" else original_text,
        source="agent_note", source_ref="run:stale-writer",
        replaces_id=original["id"] if operation == "correction" else None,
    )
    assert interleaved
    assert result["ok"] is False
    assert "changed or was deleted" in result["error"]
    assert "retry" in result["error"]
    rows = store.list_memories(limit=10)["items"]
    if concurrent_change == "delete":
        assert rows == []
    else:
        assert len(rows) == 1
        assert rows[0]["text"] == original_text
        assert rows[0]["source"] == "user_correction"
        assert rows[0]["source_ref"] == "manual:ui"
        assert rows[0]["importance"] == 9  # Stale dedup did not reinforce the user row.
        assert is_authoritative_fact(rows[0])
