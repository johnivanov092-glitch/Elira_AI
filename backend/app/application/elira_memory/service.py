from __future__ import annotations

import sqlite3
from pathlib import Path

from app.application.elira_memory import runtime as elira_memory_runtime
from app.core.data_files import sqlite_data_file
from app.core.persona_defaults import DEFAULT_PROFILE
from app.infrastructure.db.connection import connect_sqlite


DB_PATH = sqlite_data_file("elira_state.db", key_tables=("chats", "messages"))
DEFAULT_CHAT_TITLE = "Новый чат"


def _connect(path: str | Path | None = None):
    return connect_sqlite(
        path or DB_PATH,
        row_factory=sqlite3.Row,
        journal_mode=None,
    )


_VALID_TABLES = {"chats", "messages", "settings"}


def _ensure_column(conn, table: str, column: str, ddl: str):
    return elira_memory_runtime.ensure_column(
        conn=conn,
        valid_tables=_VALID_TABLES,
        table=table,
        column=column,
        ddl=ddl,
    )


def _table_exists(conn, table: str) -> bool:
    return elira_memory_runtime.table_exists(conn=conn, table=table)


def init_db():
    return elira_memory_runtime.init_db(
        connect_func=_connect,
        default_profile=DEFAULT_PROFILE,
        ensure_column_func=_ensure_column,
    )


def count_chats(path: str | Path | None = None) -> int:
    return elira_memory_runtime.count_chats(
        connect_func=lambda: _connect(path),
        table_exists_func=_table_exists,
    )


def count_messages(path: str | Path | None = None) -> int:
    return elira_memory_runtime.count_messages(
        connect_func=lambda: _connect(path),
        table_exists_func=_table_exists,
    )


def _chat_row(conn, chat_id: int):
    return elira_memory_runtime.chat_row(conn=conn, chat_id=chat_id)


def list_chats():
    return elira_memory_runtime.list_chats(connect_func=_connect)


def create_chat(title=DEFAULT_CHAT_TITLE):
    return elira_memory_runtime.create_chat(
        connect_func=_connect,
        chat_row_func=_chat_row,
        title=title,
        default_title=DEFAULT_CHAT_TITLE,
    )


def update_chat(chat_id: int, title=None, pinned=None, memory_saved=None):
    return elira_memory_runtime.update_chat(
        connect_func=_connect,
        chat_row_func=_chat_row,
        chat_id=chat_id,
        default_title=DEFAULT_CHAT_TITLE,
        title=title,
        pinned=pinned,
        memory_saved=memory_saved,
    )


def rename_chat(chat_id, title):
    return update_chat(chat_id, title=title)


def set_chat_pinned(chat_id, pinned):
    return update_chat(chat_id, pinned=pinned)


def set_chat_memory_saved(chat_id, memory_saved):
    return update_chat(chat_id, memory_saved=memory_saved)


def delete_chat(chat_id):
    return elira_memory_runtime.delete_chat(connect_func=_connect, chat_id=chat_id)


def get_messages(chat_id):
    return elira_memory_runtime.get_messages(connect_func=_connect, chat_id=chat_id)


def add_message(chat_id, role, content):
    return elira_memory_runtime.add_message(
        connect_func=_connect,
        chat_id=chat_id,
        role=role,
        content=content,
    )


init_db()
