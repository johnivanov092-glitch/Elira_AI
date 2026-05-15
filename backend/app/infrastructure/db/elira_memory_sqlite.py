from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.data_files import sqlite_data_file
from app.core.persona_defaults import DEFAULT_PROFILE


DB_PATH = sqlite_data_file("elira_state.db", key_tables=("chats", "messages"))


def _connect(path: str | Path | None = None):
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


_VALID_TABLES = {"chats", "messages", "settings"}


def _ensure_column(conn, table: str, column: str, ddl: str):
    if table not in _VALID_TABLES:
        raise ValueError(f"Invalid table name: {table}")
    safe_table = '"' + table.replace('"', '""') + '"'
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({safe_table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {safe_table} ADD COLUMN {ddl}")


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def init_db():
    conn = _connect()
    try:
        cur = conn.cursor()

        cur.execute(
            "CREATE TABLE IF NOT EXISTS chats ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        _ensure_column(conn, "chats", "pinned", "pinned INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "chats", "memory_saved", "memory_saved INTEGER NOT NULL DEFAULT 0")

        cur.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "chat_id INTEGER NOT NULL, "
            "role TEXT NOT NULL, "
            "content TEXT NOT NULL, "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ")"
        )

        cur.execute(
            "CREATE TABLE IF NOT EXISTS settings ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "ollama_context INTEGER NOT NULL DEFAULT 8192, "
            "default_model TEXT NOT NULL DEFAULT 'gemma3:4b', "
            f"agent_profile TEXT NOT NULL DEFAULT '{DEFAULT_PROFILE}'"
            ")"
        )
        cur.execute(
            "INSERT OR IGNORE INTO settings(id, ollama_context, default_model, agent_profile) "
            f"VALUES(1, 8192, 'gemma3:4b', '{DEFAULT_PROFILE}')"
        )
        conn.commit()
    finally:
        conn.close()


def count_chats(path: str | Path | None = None) -> int:
    conn = _connect(path)
    try:
        if not _table_exists(conn, "chats"):
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0])
    finally:
        conn.close()


def count_messages(path: str | Path | None = None) -> int:
    conn = _connect(path)
    try:
        if not _table_exists(conn, "messages"):
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    finally:
        conn.close()


def _chat_row(conn, chat_id: int):
    return conn.execute(
        "SELECT id, title, pinned, memory_saved, created_at, updated_at "
        "FROM chats WHERE id = ?",
        (chat_id,),
    ).fetchone()


def list_chats():
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, title, pinned, memory_saved, created_at, updated_at "
            "FROM chats ORDER BY pinned DESC, updated_at DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def create_chat(title="Новый чат"):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chats(title, pinned, memory_saved) VALUES (?, 0, 0)",
            (title or "Новый чат",),
        )
        chat_id = cur.lastrowid
        row = _chat_row(conn, chat_id)
        conn.commit()
    finally:
        conn.close()
    return dict(row)


def update_chat(chat_id: int, title=None, pinned=None, memory_saved=None):
    conn = _connect()
    try:
        current = _chat_row(conn, chat_id)
        if not current:
            return None

        next_title = current["title"] if title is None else (title or "Новый чат")
        next_pinned = current["pinned"] if pinned is None else int(bool(pinned))
        next_memory_saved = current["memory_saved"] if memory_saved is None else int(bool(memory_saved))

        conn.execute(
            "UPDATE chats "
            "SET title = ?, pinned = ?, memory_saved = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (next_title, next_pinned, next_memory_saved, chat_id),
        )
        row = _chat_row(conn, chat_id)
        conn.commit()
    finally:
        conn.close()
    return dict(row) if row else None


def rename_chat(chat_id, title):
    return update_chat(chat_id, title=title)


def set_chat_pinned(chat_id, pinned):
    return update_chat(chat_id, pinned=pinned)


def set_chat_memory_saved(chat_id, memory_saved):
    return update_chat(chat_id, memory_saved=memory_saved)


def delete_chat(chat_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        conn.commit()
    finally:
        conn.close()


def get_messages(chat_id):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, chat_id, role, content, created_at "
            "FROM messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def add_message(chat_id, role, content):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages(chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content),
        )
        message_id = cur.lastrowid
        conn.execute(
            "UPDATE chats SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (chat_id,),
        )
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    return dict(row)


init_db()
