"""Library SQLite persistence — file metadata storage for /api/lib."""
from __future__ import annotations

import sqlite3

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

DB_PATH = sqlite_data_file("library.db", key_tables=("files",))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    size INTEGER DEFAULT 0,
    type TEXT DEFAULT 'unknown',
    preview TEXT DEFAULT '',
    use_in_context INTEGER DEFAULT 1,
    source TEXT DEFAULT 'upload',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    c = _conn()
    c.execute(_CREATE_SQL)
    _ensure_column(c, "files", "stored_path", "stored_path TEXT DEFAULT ''")
    _ensure_column(c, "files", "sha256", "sha256 TEXT DEFAULT ''")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lib_ctx ON files(use_in_context)")
    c.commit()
    c.close()


init_db()


def list_files() -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM files ORDER BY created_at DESC, id DESC").fetchall()
    c.close()
    return [dict(r) for r in rows]


def delete_file(file_id: int) -> str | None:
    c = _conn()
    row = c.execute("SELECT stored_path FROM files WHERE id = ?", (file_id,)).fetchone()
    c.execute("DELETE FROM files WHERE id = ?", (file_id,))
    c.commit()
    c.close()
    return row["stored_path"] if row else None


def search_files(query: str, limit: int = 20) -> list[dict]:
    q = f"%{query.strip()}%"
    c = _conn()
    rows = c.execute(
        "SELECT * FROM files WHERE name LIKE ? OR preview LIKE ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (q, q, limit),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_context_files(limit: int = 10) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT id, name, preview, stored_path FROM files WHERE use_in_context = 1 AND preview != '' ORDER BY created_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]
