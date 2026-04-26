from __future__ import annotations

from typing import Any, Callable


def ensure_column(
    *,
    conn: Any,
    valid_tables: set[str],
    table: str,
    column: str,
    ddl: str,
) -> None:
    if table not in valid_tables:
        raise ValueError(f"Invalid table name: {table}")
    safe_table = '"' + table.replace('"', '""') + '"'
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({safe_table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {safe_table} ADD COLUMN {ddl}")


def table_exists(*, conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def init_db(
    *,
    connect_func: Callable[[], Any],
    default_profile: str,
    ensure_column_func: Callable[[Any, str, str, str], None],
) -> None:
    conn = connect_func()
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
        ensure_column_func(conn, "chats", "pinned", "pinned INTEGER NOT NULL DEFAULT 0")
        ensure_column_func(conn, "chats", "memory_saved", "memory_saved INTEGER NOT NULL DEFAULT 0")

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
            f"agent_profile TEXT NOT NULL DEFAULT '{default_profile}'"
            ")"
        )
        cur.execute(
            "INSERT OR IGNORE INTO settings(id, ollama_context, default_model, agent_profile) "
            f"VALUES(1, 8192, 'gemma3:4b', '{default_profile}')"
        )
        conn.commit()
    finally:
        conn.close()


def count_chats(
    *,
    connect_func: Callable[[], Any],
    table_exists_func: Callable[[Any, str], bool],
) -> int:
    conn = connect_func()
    try:
        if not table_exists_func(conn, "chats"):
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0])
    finally:
        conn.close()


def count_messages(
    *,
    connect_func: Callable[[], Any],
    table_exists_func: Callable[[Any, str], bool],
) -> int:
    conn = connect_func()
    try:
        if not table_exists_func(conn, "messages"):
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    finally:
        conn.close()


def chat_row(*, conn: Any, chat_id: int) -> Any:
    return conn.execute(
        "SELECT id, title, pinned, memory_saved, created_at, updated_at "
        "FROM chats WHERE id = ?",
        (chat_id,),
    ).fetchone()


def list_chats(*, connect_func: Callable[[], Any]) -> list[dict[str, Any]]:
    conn = connect_func()
    try:
        rows = conn.execute(
            "SELECT id, title, pinned, memory_saved, created_at, updated_at "
            "FROM chats ORDER BY pinned DESC, updated_at DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def create_chat(
    *,
    connect_func: Callable[[], Any],
    chat_row_func: Callable[[Any, int], Any],
    title: str,
    default_title: str,
) -> dict[str, Any]:
    conn = connect_func()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chats(title, pinned, memory_saved) VALUES (?, 0, 0)",
            (title or default_title,),
        )
        chat_id = cur.lastrowid
        row = chat_row_func(conn, chat_id)
        conn.commit()
    finally:
        conn.close()
    return dict(row)

def update_chat(
    *,
    connect_func: Callable[[], Any],
    chat_row_func: Callable[[Any, int], Any],
    chat_id: int,
    default_title: str,
    title: str | None = None,
    pinned: bool | int | None = None,
    memory_saved: bool | int | None = None,
) -> dict[str, Any] | None:
    conn = connect_func()
    try:
        current = chat_row_func(conn, chat_id)
        if not current:
            return None

        next_title = current["title"] if title is None else (title or default_title)
        next_pinned = current["pinned"] if pinned is None else int(bool(pinned))
        next_memory_saved = current["memory_saved"] if memory_saved is None else int(bool(memory_saved))

        conn.execute(
            "UPDATE chats "
            "SET title = ?, pinned = ?, memory_saved = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (next_title, next_pinned, next_memory_saved, chat_id),
        )
        row = chat_row_func(conn, chat_id)
        conn.commit()
    finally:
        conn.close()
    return dict(row) if row else None


def delete_chat(*, connect_func: Callable[[], Any], chat_id: int) -> None:
    conn = connect_func()
    try:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        conn.commit()
    finally:
        conn.close()


def get_messages(*, connect_func: Callable[[], Any], chat_id: int) -> list[dict[str, Any]]:
    conn = connect_func()
    try:
        rows = conn.execute(
            "SELECT id, chat_id, role, content, created_at "
            "FROM messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def add_message(
    *,
    connect_func: Callable[[], Any],
    chat_id: int,
    role: str,
    content: str,
) -> dict[str, Any]:
    conn = connect_func()
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
