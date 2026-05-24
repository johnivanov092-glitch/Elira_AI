"""Elira state SQLite compatibility facade."""

from __future__ import annotations

from app.application.elira_memory.service import (
    DB_PATH,
    DEFAULT_CHAT_TITLE,
    _chat_row,
    _connect,
    _ensure_column,
    _table_exists,
    add_message,
    count_chats,
    count_messages,
    create_chat,
    delete_chat,
    get_messages,
    init_db,
    list_chats,
    rename_chat,
    set_chat_memory_saved,
    set_chat_pinned,
    update_chat,
)

__all__ = [
    "DB_PATH",
    "DEFAULT_CHAT_TITLE",
    "_chat_row",
    "_connect",
    "_ensure_column",
    "_table_exists",
    "add_message",
    "count_chats",
    "count_messages",
    "create_chat",
    "delete_chat",
    "get_messages",
    "init_db",
    "list_chats",
    "rename_chat",
    "set_chat_memory_saved",
    "set_chat_pinned",
    "update_chat",
]
