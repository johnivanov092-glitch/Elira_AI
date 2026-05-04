"""Tests for application/elira_memory (pure callback runtime) and
application/elira_memory_sqlite (high-level CRUD over patched DB_PATH)."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.elira_memory import runtime as em_rt         # noqa: E402
import app.application.elira_memory_sqlite.runtime as msql        # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers for testing elira_memory/runtime directly with a temp DB
# ─────────────────────────────────────────────────────────────────────────────

def _make_connect_func(db_path: Path):
    """Return a connect_func closure that always opens db_path with Row factory."""
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    return _connect


def _make_ensure_column(valid_tables):
    def _ensure(conn, table, column, ddl):
        em_rt.ensure_column(
            conn=conn, valid_tables=valid_tables, table=table, column=column, ddl=ddl
        )
    return _ensure


# ─────────────────────────────────────────────────────────────────────────────
# elira_memory/runtime — pure helpers (no DB)
# ─────────────────────────────────────────────────────────────────────────────

class EnsureColumnTest(unittest.TestCase):
    def setUp(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("CREATE TABLE chats (id INTEGER PRIMARY KEY, title TEXT)")
        self._conn.commit()

    def tearDown(self) -> None:
        self._conn.close()

    def test_adds_missing_column(self) -> None:
        em_rt.ensure_column(
            conn=self._conn, valid_tables={"chats"},
            table="chats", column="pinned", ddl="pinned INTEGER NOT NULL DEFAULT 0"
        )
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(chats)").fetchall()}
        self.assertIn("pinned", cols)

    def test_no_op_if_column_exists(self) -> None:
        # Should not raise even if column already present
        em_rt.ensure_column(
            conn=self._conn, valid_tables={"chats"},
            table="chats", column="title", ddl="title TEXT"
        )
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(chats)").fetchall()}
        self.assertIn("title", cols)

    def test_invalid_table_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            em_rt.ensure_column(
                conn=self._conn, valid_tables={"chats"},
                table="malicious_table", column="x", ddl="x TEXT"
            )


class TableExistsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("CREATE TABLE existing_table (id INTEGER PRIMARY KEY)")
        self._conn.commit()

    def tearDown(self) -> None:
        self._conn.close()

    def test_existing_table_returns_true(self) -> None:
        self.assertTrue(em_rt.table_exists(conn=self._conn, table="existing_table"))

    def test_missing_table_returns_false(self) -> None:
        self.assertFalse(em_rt.table_exists(conn=self._conn, table="no_such_table"))


# ─────────────────────────────────────────────────────────────────────────────
# elira_memory/runtime — init_db + CRUD via temp file (avoid :memory: state loss)
# ─────────────────────────────────────────────────────────────────────────────

class EliraMemoryRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "test.db"
        self._connect = _make_connect_func(self._db_path)
        self._ensure_col = _make_ensure_column({"chats", "messages", "settings"})
        em_rt.init_db(
            connect_func=self._connect,
            default_profile="default",
            ensure_column_func=self._ensure_col,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_tables_created(self) -> None:
        conn = self._connect()
        try:
            self.assertTrue(em_rt.table_exists(conn=conn, table="chats"))
            self.assertTrue(em_rt.table_exists(conn=conn, table="messages"))
            self.assertTrue(em_rt.table_exists(conn=conn, table="settings"))
        finally:
            conn.close()

    def test_count_chats_empty(self) -> None:
        count = em_rt.count_chats(
            connect_func=self._connect,
            table_exists_func=lambda conn, t: em_rt.table_exists(conn=conn, table=t),
        )
        self.assertEqual(count, 0)

    def test_count_messages_empty(self) -> None:
        count = em_rt.count_messages(
            connect_func=self._connect,
            table_exists_func=lambda conn, t: em_rt.table_exists(conn=conn, table=t),
        )
        self.assertEqual(count, 0)

    def test_list_chats_empty(self) -> None:
        chats = em_rt.list_chats(connect_func=self._connect)
        self.assertEqual(chats, [])

    def test_create_chat(self) -> None:
        def _chat_row(conn, chat_id):
            return em_rt.chat_row(conn=conn, chat_id=chat_id)
        chat = em_rt.create_chat(
            connect_func=self._connect,
            chat_row_func=_chat_row,
            title="Test Chat",
            default_title="New Chat",
        )
        self.assertIn("id", chat)
        self.assertEqual(chat["title"], "Test Chat")

    def test_create_chat_empty_title_uses_default(self) -> None:
        def _chat_row(conn, chat_id):
            return em_rt.chat_row(conn=conn, chat_id=chat_id)
        chat = em_rt.create_chat(
            connect_func=self._connect,
            chat_row_func=_chat_row,
            title="",
            default_title="Default Title",
        )
        self.assertEqual(chat["title"], "Default Title")

    def test_list_chats_after_create(self) -> None:
        def _chat_row(conn, chat_id):
            return em_rt.chat_row(conn=conn, chat_id=chat_id)
        em_rt.create_chat(
            connect_func=self._connect, chat_row_func=_chat_row,
            title="My Chat", default_title="New"
        )
        chats = em_rt.list_chats(connect_func=self._connect)
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["title"], "My Chat")

    def test_update_chat_title(self) -> None:
        def _chat_row(conn, chat_id):
            return em_rt.chat_row(conn=conn, chat_id=chat_id)
        chat = em_rt.create_chat(
            connect_func=self._connect, chat_row_func=_chat_row,
            title="Old", default_title="New"
        )
        updated = em_rt.update_chat(
            connect_func=self._connect, chat_row_func=_chat_row,
            chat_id=chat["id"], default_title="New", title="Renamed"
        )
        self.assertEqual(updated["title"], "Renamed")

    def test_update_chat_pinned(self) -> None:
        def _chat_row(conn, chat_id):
            return em_rt.chat_row(conn=conn, chat_id=chat_id)
        chat = em_rt.create_chat(
            connect_func=self._connect, chat_row_func=_chat_row,
            title="A", default_title="New"
        )
        updated = em_rt.update_chat(
            connect_func=self._connect, chat_row_func=_chat_row,
            chat_id=chat["id"], default_title="New", pinned=True
        )
        self.assertEqual(updated["pinned"], 1)

    def test_update_chat_not_found_returns_none(self) -> None:
        def _chat_row(conn, chat_id):
            return em_rt.chat_row(conn=conn, chat_id=chat_id)
        result = em_rt.update_chat(
            connect_func=self._connect, chat_row_func=_chat_row,
            chat_id=9999, default_title="New"
        )
        self.assertIsNone(result)

    def test_delete_chat(self) -> None:
        def _chat_row(conn, chat_id):
            return em_rt.chat_row(conn=conn, chat_id=chat_id)
        chat = em_rt.create_chat(
            connect_func=self._connect, chat_row_func=_chat_row,
            title="To Delete", default_title="New"
        )
        em_rt.delete_chat(connect_func=self._connect, chat_id=chat["id"])
        chats = em_rt.list_chats(connect_func=self._connect)
        self.assertEqual(chats, [])

    def test_add_and_get_messages(self) -> None:
        def _chat_row(conn, chat_id):
            return em_rt.chat_row(conn=conn, chat_id=chat_id)
        chat = em_rt.create_chat(
            connect_func=self._connect, chat_row_func=_chat_row,
            title="Chat", default_title="New"
        )
        em_rt.add_message(
            connect_func=self._connect, chat_id=chat["id"],
            role="user", content="Hello"
        )
        em_rt.add_message(
            connect_func=self._connect, chat_id=chat["id"],
            role="assistant", content="Hi there"
        )
        messages = em_rt.get_messages(connect_func=self._connect, chat_id=chat["id"])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[1]["content"], "Hi there")

    def test_count_messages_after_add(self) -> None:
        def _chat_row(conn, chat_id):
            return em_rt.chat_row(conn=conn, chat_id=chat_id)
        chat = em_rt.create_chat(
            connect_func=self._connect, chat_row_func=_chat_row,
            title="C", default_title="N"
        )
        em_rt.add_message(
            connect_func=self._connect, chat_id=chat["id"], role="user", content="msg"
        )
        count = em_rt.count_messages(
            connect_func=self._connect,
            table_exists_func=lambda conn, t: em_rt.table_exists(conn=conn, table=t),
        )
        self.assertEqual(count, 1)

    def test_delete_chat_cascades_messages(self) -> None:
        def _chat_row(conn, chat_id):
            return em_rt.chat_row(conn=conn, chat_id=chat_id)
        chat = em_rt.create_chat(
            connect_func=self._connect, chat_row_func=_chat_row,
            title="With msgs", default_title="New"
        )
        em_rt.add_message(
            connect_func=self._connect, chat_id=chat["id"], role="user", content="bye"
        )
        em_rt.delete_chat(connect_func=self._connect, chat_id=chat["id"])
        msgs = em_rt.get_messages(connect_func=self._connect, chat_id=chat["id"])
        self.assertEqual(msgs, [])


# ─────────────────────────────────────────────────────────────────────────────
# elira_memory_sqlite/runtime — high-level CRUD (patched DB_PATH)
# ─────────────────────────────────────────────────────────────────────────────

class EliraMemorySQLiteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = msql.DB_PATH
        msql.DB_PATH = Path(self._tmpdir.name) / "elira_memory.db"
        msql.init_db()

    def tearDown(self) -> None:
        msql.DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_count_chats_empty(self) -> None:
        self.assertEqual(msql.count_chats(), 0)

    def test_count_messages_empty(self) -> None:
        self.assertEqual(msql.count_messages(), 0)

    def test_list_chats_empty(self) -> None:
        self.assertEqual(msql.list_chats(), [])

    def test_create_chat_returns_dict(self) -> None:
        chat = msql.create_chat("My Chat")
        self.assertIn("id", chat)
        self.assertEqual(chat["title"], "My Chat")

    def test_create_chat_default_title(self) -> None:
        chat = msql.create_chat()
        self.assertEqual(chat["title"], msql.DEFAULT_CHAT_TITLE)

    def test_count_chats_after_create(self) -> None:
        msql.create_chat("A")
        msql.create_chat("B")
        self.assertEqual(msql.count_chats(), 2)

    def test_list_chats_after_create(self) -> None:
        msql.create_chat("Chat 1")
        msql.create_chat("Chat 2")
        chats = msql.list_chats()
        self.assertEqual(len(chats), 2)

    def test_rename_chat(self) -> None:
        chat = msql.create_chat("Original")
        updated = msql.rename_chat(chat["id"], "Renamed")
        self.assertEqual(updated["title"], "Renamed")

    def test_set_chat_pinned(self) -> None:
        chat = msql.create_chat("Pinnable")
        updated = msql.set_chat_pinned(chat["id"], True)
        self.assertEqual(updated["pinned"], 1)

    def test_set_chat_memory_saved(self) -> None:
        chat = msql.create_chat("With Memory")
        updated = msql.set_chat_memory_saved(chat["id"], True)
        self.assertEqual(updated["memory_saved"], 1)

    def test_delete_chat(self) -> None:
        chat = msql.create_chat("To Delete")
        msql.delete_chat(chat["id"])
        self.assertEqual(msql.count_chats(), 0)

    def test_add_message_and_get_messages(self) -> None:
        chat = msql.create_chat("Msg Chat")
        msql.add_message(chat["id"], "user", "Hello!")
        msql.add_message(chat["id"], "assistant", "Hi!")
        messages = msql.get_messages(chat["id"])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[1]["content"], "Hi!")

    def test_count_messages_after_add(self) -> None:
        chat = msql.create_chat("X")
        msql.add_message(chat["id"], "user", "msg")
        self.assertEqual(msql.count_messages(), 1)

    def test_get_messages_empty_for_nonexistent_chat(self) -> None:
        messages = msql.get_messages(9999)
        self.assertEqual(messages, [])

    def test_delete_chat_removes_messages(self) -> None:
        chat = msql.create_chat("With msg")
        msql.add_message(chat["id"], "user", "bye")
        msql.delete_chat(chat["id"])
        self.assertEqual(msql.count_messages(), 0)

    def test_update_chat_not_found_returns_none(self) -> None:
        result = msql.update_chat(9999, title="X")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
