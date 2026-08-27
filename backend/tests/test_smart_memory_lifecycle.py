from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.smart_memory import store  # noqa: E402


@pytest.fixture()
def isolated_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "smart_memory.db"
    monkeypatch.setattr(store, "DB_PATH", db_path)
    store.init_memory_db()
    return db_path


def test_user_correction_supersedes_same_topic_fact(isolated_memory: Path) -> None:
    first = store.add_memory(
        "Пользователь предпочитает Python для автоматизации.",
        category="preference",
        source="user",
        importance=8,
    )
    corrected = store.add_memory(
        "Пользователь предпочитает Go для автоматизации.",
        category="preference",
        source="user_correction",
        importance=10,
    )

    assert corrected["ok"] is True
    assert corrected["action"] == "corrected"
    assert corrected["id"] == first["id"]
    assert corrected["previous_text"] == "Пользователь предпочитает Python для автоматизации."
    rows = store.list_memories(limit=20)["items"]
    assert len(rows) == 1
    assert rows[0]["text"] == "Пользователь предпочитает Go для автоматизации."
    assert rows[0]["source"] == "user_correction"
    assert rows[0]["importance"] == 10


def test_correction_promotes_exact_existing_fact_to_authoritative_source(isolated_memory: Path) -> None:
    first = store.add_memory("Проект называется Elira.", source="auto", importance=4)
    corrected = store.add_memory(
        "Проект называется Elira.",
        category="user_fact",
        source="user_correction",
        importance=10,
    )

    assert corrected["action"] == "corrected"
    assert corrected["id"] == first["id"]
    row = store.list_memories(limit=1)["items"][0]
    assert row["category"] == "user_fact"
    assert row["source"] == "user_correction"
    assert row["importance"] == 10


def test_explicit_correction_target_handles_complete_rephrasing(isolated_memory: Path) -> None:
    first = store.add_memory("Меня зовут Иван.", source="user", importance=8)
    corrected = store.add_memory(
        "Моё имя Пётр.",
        category="user_fact",
        source="manual",
        importance=10,
        replaces_id=first["id"],
    )

    assert corrected["action"] == "corrected"
    assert corrected["id"] == first["id"]
    rows = store.list_memories(limit=20)["items"]
    assert len(rows) == 1
    assert rows[0]["text"] == "Моё имя Пётр."
    assert rows[0]["source"] == "user_correction"


def test_unrelated_correction_does_not_delete_another_fact(isolated_memory: Path) -> None:
    first = store.add_memory("Пользователя зовут Евгений.", source="user", importance=8)
    second = store.add_memory(
        "Пользователь предпочитает тёмную тему.",
        category="preference",
        source="user_correction",
        importance=10,
    )

    assert second["action"] == "created"
    assert second["id"] != first["id"]
    assert store.list_memories(limit=20)["count"] == 2


def test_prune_volatile_memories_is_age_bounded_and_dry_runnable(isolated_memory: Path) -> None:
    old = store.add_memory(
        "Активная модель сейчас Qwen.",
        category="volatile_fact",
        source="user",
    )
    recent = store.add_memory(
        "Uptime сервера сейчас 5 минут.",
        category="volatile_fact",
        source="user",
    )
    durable = store.add_memory(
        "Пользователь предпочитает краткие ответы.",
        category="preference",
        source="user",
    )
    conn = sqlite3.connect(isolated_memory)
    try:
        conn.execute(
            "UPDATE memories SET updated_at = datetime('now', '-30 days') WHERE id = ?",
            (old["id"],),
        )
        conn.commit()
    finally:
        conn.close()

    preview = store.prune_volatile_memories(max_age_days=7, dry_run=True)
    assert preview["candidates"] == 1
    assert preview["pruned"] == 0
    assert preview["sample"][0]["id"] == old["id"]

    result = store.prune_volatile_memories(max_age_days=7, dry_run=False)
    assert result["pruned"] == 1
    remaining = {item["id"] for item in store.list_memories(limit=20)["items"]}
    assert old["id"] not in remaining
    assert recent["id"] in remaining
    assert durable["id"] in remaining
