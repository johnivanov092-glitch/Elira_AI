"""Tests for app.application.elira_memory.runtime - all functions use
callback injection and operate on sqlite3.Row connections, so the entire
module is testable with in-memory SQLite and no real DB or FS.

Covers:
  table_exists, ensure_column, init_db,
  count_chats, count_messages,
  chat_row, list_chats, create_chat, update_chat, delete_chat,
  get_messages, add_message
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.elira_memory.runtime import (  # noqa: E402
    table_exists,
    ensure_column,
    init_db,
    count_chats,
    count_messages,
    chat_row,
    list_chats,
    create_chat,
    update_chat,
    delete_chat,
    get_messages,
    add_message,
)


# ---
# Shared in-memory SQLite helpers
# ---

class _SharedConn:
    """Proxy that keeps the shared in-memory DB alive across connect_func calls."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def cursor(self):
        return self._db.cursor()

    def execute(self, *a, **kw):
        return self._db.execute(*a, **kw)

    def executescript(self, *a, **kw):
        return self._db.executescript(*a, **kw)

    def commit(self):
        self._db.commit()

    def close(self):
        pass  # keep alive


def _make_db() -> tuple[sqlite3.Connection, Any]:
    """Return (raw_db, connect_func) using a fresh in-memory SQLite."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row

    def connect():
        return _SharedConn(db)

    return db, connect


def _ensure_noop(conn: Any, table: str, column: str, ddl: str) -> None:
    """No-op ensure_column stub (column always assumed present)."""
    pass


def _bootstrapped_db():
    """Return (raw_db, connect_func) with schema already initialised."""
    db, conn_func = _make_db()
    init_db(
        connect_func=conn_func,
        default_profile="default",
        ensure_column_func=lambda conn, tbl, col, ddl: ensure_column(
            conn=conn,
            valid_tables={"chats", "messages", "settings"},
            table=tbl,
            column=col,
            ddl=ddl,
        ),
    )
    return db, conn_func


# ---
# table_exists
# ---

class TableExistsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _make_db()

    def test_missing_table_returns_false(self) -> None:
        conn = self._conn_func()
        self.assertFalse(table_exists(conn=conn, table="nonexistent"))

    def test_existing_table_returns_true(self) -> None:
        self._db.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY)")
        conn = self._conn_func()
        self.assertTrue(table_exists(conn=conn, table="foo"))

    def test_returns_bool(self) -> None:
        conn = self._conn_func()
        self.assertIsInstance(table_exists(conn=conn, table="x"), bool)


# ---
# ensure_column
# ---

class EnsureColumnTest(unittest.TestCase):
    _VALID = {"chats", "messages", "settings"}

    def setUp(self) -> None:
        self._db, self._conn_func = _make_db()
        self._db.execute(
            "CREATE TABLE chats (id INTEGER PRIMARY KEY, title TEXT NOT NULL)"
        )
        self._db.commit()

    def test_invalid_table_raises_value_error(self) -> None:
        conn = self._conn_func()
        with self.assertRaises(ValueError):
            ensure_column(
                conn=conn,
                valid_tables=self._VALID,
                table="evil_table",
                column="x",
                ddl="x TEXT",
            )

    def test_adds_missing_column(self) -> None:
        conn = self._conn_func()
        ensure_column(
            conn=conn,
            valid_tables=self._VALID,
            table="chats",
            column="pinned",
            ddl="pinned INTEGER NOT NULL DEFAULT 0",
        )
        cols = {row["name"] for row in self._db.execute("PRAGMA table_info(chats)").fetchall()}
        self.assertIn("pinned", cols)

    def test_existing_column_not_duplicated(self) -> None:
        conn = self._conn_func()
        # Add once
        ensure_column(
            conn=conn, valid_tables=self._VALID, table="chats",
            column="pinned", ddl="pinned INTEGER NOT NULL DEFAULT 0",
        )
        # Add again; should not raise.
        try:
            ensure_column(
                conn=conn, valid_tables=self._VALID, table="chats",
                column="pinned", ddl="pinned INTEGER NOT NULL DEFAULT 0",
            )
        except Exception as exc:
            self.fail(f"ensure_column raised on existing column: {exc}")


# ---
# init_db
# ---

class InitDbTest(unittest.TestCase):
    def test_creates_chats_table(self) -> None:
        db, conn_func = _make_db()
        init_db(connect_func=conn_func, default_profile="default",
                ensure_column_func=_ensure_noop)
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertIn("chats", tables)

    def test_creates_messages_table(self) -> None:
        db, conn_func = _make_db()
        init_db(connect_func=conn_func, default_profile="default",
                ensure_column_func=_ensure_noop)
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertIn("messages", tables)

    def test_creates_settings_table(self) -> None:
        db, conn_func = _make_db()
        init_db(connect_func=conn_func, default_profile="default",
                ensure_column_func=_ensure_noop)
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertIn("settings", tables)

    def test_idempotent(self) -> None:
        _, conn_func = _make_db()
        init_db(connect_func=conn_func, default_profile="default",
                ensure_column_func=_ensure_noop)
        init_db(connect_func=conn_func, default_profile="default",
                ensure_column_func=_ensure_noop)

    def test_settings_row_created(self) -> None:
        db, conn_func = _make_db()
        init_db(connect_func=conn_func, default_profile="work",
                ensure_column_func=_ensure_noop)
        row = db.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        self.assertIsNotNone(row)


# ---
# count_chats / count_messages
# ---

class CountTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped_db()

    def _te(self, conn, table):
        return table_exists(conn=conn, table=table)

    def test_count_chats_empty(self) -> None:
        self.assertEqual(
            count_chats(connect_func=self._conn_func, table_exists_func=self._te), 0
        )

    def test_count_messages_empty(self) -> None:
        self.assertEqual(
            count_messages(connect_func=self._conn_func, table_exists_func=self._te), 0
        )

    def test_count_chats_after_insert(self) -> None:
        self._db.execute("INSERT INTO chats(title) VALUES ('test')")
        self._db.commit()
        self.assertEqual(
            count_chats(connect_func=self._conn_func, table_exists_func=self._te), 1
        )

    def test_count_chats_table_missing_returns_zero(self) -> None:
        # Use a DB without the chats table
        db2 = sqlite3.connect(":memory:", check_same_thread=False)
        db2.row_factory = sqlite3.Row

        def conn2():
            return _SharedConn(db2)

        result = count_chats(
            connect_func=conn2,
            table_exists_func=lambda c, t: False,
        )
        self.assertEqual(result, 0)


# ---
# create_chat / list_chats / chat_row
# ---

class CreateListChatTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped_db()

    def _chat_row_func(self, conn: Any, cid: int) -> Any:
        return chat_row(conn=conn, chat_id=cid)

    def test_create_chat_returns_dict(self) -> None:
        result = create_chat(
            connect_func=self._conn_func,
            chat_row_func=self._chat_row_func,
            title="My chat",
            default_title="Новый чат",
        )
        self.assertIsInstance(result, dict)

    def test_create_chat_has_id(self) -> None:
        result = create_chat(
            connect_func=self._conn_func,
            chat_row_func=self._chat_row_func,
            title="Test",
            default_title="Новый чат",
        )
        self.assertIn("id", result)

    def test_create_chat_title_stored(self) -> None:
        result = create_chat(
            connect_func=self._conn_func,
            chat_row_func=self._chat_row_func,
            title="Work chat",
            default_title="Новый чат",
        )
        self.assertEqual(result["title"], "Work chat")

    def test_empty_title_uses_default(self) -> None:
        result = create_chat(
            connect_func=self._conn_func,
            chat_row_func=self._chat_row_func,
            title="",
            default_title="Новый чат",
        )
        self.assertEqual(result["title"], "Новый чат")

    def test_list_chats_returns_list(self) -> None:
        self.assertIsInstance(list_chats(connect_func=self._conn_func), list)

    def test_list_chats_empty_at_start(self) -> None:
        self.assertEqual(list_chats(connect_func=self._conn_func), [])

    def test_list_chats_after_create(self) -> None:
        create_chat(
            connect_func=self._conn_func,
            chat_row_func=self._chat_row_func,
            title="Listed chat",
            default_title="Новый чат",
        )
        chats = list_chats(connect_func=self._conn_func)
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["title"], "Listed chat")

    def test_list_chats_multiple(self) -> None:
        for i in range(3):
            create_chat(
                connect_func=self._conn_func,
                chat_row_func=self._chat_row_func,
                title=f"Chat {i}",
                default_title="Новый чат",
            )
        self.assertEqual(len(list_chats(connect_func=self._conn_func)), 3)


# ---
# update_chat
# ---

class UpdateChatTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped_db()
        self._chat = create_chat(
            connect_func=self._conn_func,
            chat_row_func=lambda conn, cid: chat_row(conn=conn, chat_id=cid),
            title="Original",
            default_title="Новый чат",
        )
        self._chat_id = self._chat["id"]

    def _update(self, **kwargs):
        return update_chat(
            connect_func=self._conn_func,
            chat_row_func=lambda conn, cid: chat_row(conn=conn, chat_id=cid),
            chat_id=self._chat_id,
            default_title="Новый чат",
            **kwargs,
        )

    def test_returns_dict_on_success(self) -> None:
        self.assertIsInstance(self._update(title="Updated"), dict)

    def test_title_updated(self) -> None:
        result = self._update(title="New title")
        self.assertEqual(result["title"], "New title")

    def test_pinned_updated(self) -> None:
        result = self._update(pinned=True)
        self.assertEqual(result["pinned"], 1)

    def test_pinned_false(self) -> None:
        self._update(pinned=True)
        result = self._update(pinned=False)
        self.assertEqual(result["pinned"], 0)

    def test_memory_saved_updated(self) -> None:
        result = self._update(memory_saved=True)
        self.assertEqual(result["memory_saved"], 1)

    def test_nonexistent_chat_returns_none(self) -> None:
        result = update_chat(
            connect_func=self._conn_func,
            chat_row_func=lambda conn, cid: chat_row(conn=conn, chat_id=cid),
            chat_id=9999,
            default_title="Новый чат",
            title="x",
        )
        self.assertIsNone(result)


# ---
# delete_chat
# ---

class DeleteChatTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped_db()
        self._chat = create_chat(
            connect_func=self._conn_func,
            chat_row_func=lambda conn, cid: chat_row(conn=conn, chat_id=cid),
            title="To delete",
            default_title="Новый чат",
        )
        self._chat_id = self._chat["id"]

    def test_delete_removes_chat(self) -> None:
        delete_chat(connect_func=self._conn_func, chat_id=self._chat_id)
        chats = list_chats(connect_func=self._conn_func)
        self.assertEqual(len(chats), 0)

    def test_delete_nonexistent_does_not_raise(self) -> None:
        try:
            delete_chat(connect_func=self._conn_func, chat_id=9999)
        except Exception as exc:
            self.fail(f"delete_chat raised: {exc}")

    def test_delete_cascades_messages(self) -> None:
        add_message(
            connect_func=self._conn_func,
            chat_id=self._chat_id,
            role="user",
            content="hello",
        )
        delete_chat(connect_func=self._conn_func, chat_id=self._chat_id)
        msgs = get_messages(connect_func=self._conn_func, chat_id=self._chat_id)
        self.assertEqual(msgs, [])


# ---
# add_message / get_messages
# ---

class AddGetMessagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db, self._conn_func = _bootstrapped_db()
        self._chat = create_chat(
            connect_func=self._conn_func,
            chat_row_func=lambda conn, cid: chat_row(conn=conn, chat_id=cid),
            title="Msg chat",
            default_title="Новый чат",
        )
        self._chat_id = self._chat["id"]

    def test_get_messages_empty(self) -> None:
        msgs = get_messages(connect_func=self._conn_func, chat_id=self._chat_id)
        self.assertEqual(msgs, [])

    def test_add_message_returns_dict(self) -> None:
        result = add_message(
            connect_func=self._conn_func,
            chat_id=self._chat_id,
            role="user",
            content="Hello!",
        )
        self.assertIsInstance(result, dict)

    def test_add_message_content_stored(self) -> None:
        add_message(
            connect_func=self._conn_func,
            chat_id=self._chat_id,
            role="user",
            content="Hello world",
        )
        msgs = get_messages(connect_func=self._conn_func, chat_id=self._chat_id)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "Hello world")

    def test_add_message_role_stored(self) -> None:
        add_message(
            connect_func=self._conn_func,
            chat_id=self._chat_id,
            role="assistant",
            content="Hi",
        )
        msgs = get_messages(connect_func=self._conn_func, chat_id=self._chat_id)
        self.assertEqual(msgs[0]["role"], "assistant")

    def test_messages_ordered_by_id(self) -> None:
        add_message(connect_func=self._conn_func, chat_id=self._chat_id, role="user", content="first")
        add_message(connect_func=self._conn_func, chat_id=self._chat_id, role="assistant", content="second")
        msgs = get_messages(connect_func=self._conn_func, chat_id=self._chat_id)
        self.assertEqual(msgs[0]["content"], "first")
        self.assertEqual(msgs[1]["content"], "second")

    def test_messages_scoped_to_chat(self) -> None:
        other_chat = create_chat(
            connect_func=self._conn_func,
            chat_row_func=lambda conn, cid: chat_row(conn=conn, chat_id=cid),
            title="Other",
            default_title="Новый чат",
        )
        add_message(
            connect_func=self._conn_func,
            chat_id=other_chat["id"],
            role="user",
            content="other chat msg",
        )
        msgs = get_messages(connect_func=self._conn_func, chat_id=self._chat_id)
        self.assertEqual(msgs, [])

    def test_multiple_messages_count(self) -> None:
        for i in range(5):
            add_message(
                connect_func=self._conn_func,
                chat_id=self._chat_id,
                role="user",
                content=f"msg {i}",
            )
        msgs = get_messages(connect_func=self._conn_func, chat_id=self._chat_id)
        self.assertEqual(len(msgs), 5)


if __name__ == "__main__":
    unittest.main()
