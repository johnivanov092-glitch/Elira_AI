# -*- coding: utf-8 -*-
"""Backward-compatible shim — all logic lives in application/elira_memory_sqlite."""
from __future__ import annotations

from app.application.elira_memory_sqlite.runtime import (  # noqa: F401
    DB_PATH,
    DEFAULT_CHAT_TITLE,
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
