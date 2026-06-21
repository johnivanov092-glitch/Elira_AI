from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from app.core.data_files import data_file
from app.infrastructure.db.connection import connect_sqlite


DB_PATH = data_file("chat_agent.db")
_WORD_RE = re.compile(r"[\w\u0400-\u04FF]+", re.UNICODE)
_NAME_PATTERNS = [
    re.compile(r"(?:^|\b)(?:меня зовут|мо[её] имя)\s+([^\n,.;!?]{2,80})", re.IGNORECASE | re.UNICODE),
    re.compile(r"(?:^|\b)(?:зови меня|называй меня)\s+([^\n,.;!?]{2,80})", re.IGNORECASE | re.UNICODE),
    re.compile(r"(?:^|\b)(?:my name is|call me)\s+([^\n,.;!?]{2,80})", re.IGNORECASE | re.UNICODE),
]
_REMEMBER_RE = re.compile(
    r"^\s*(?:elira[,\s]+)?(?:запомни|сохрани в память|remember|save this)\s*:?\s*(.+)$",
    re.IGNORECASE | re.UNICODE | re.DOTALL,
)
_DIRECT_NAME_QUERY_RE = re.compile(
    r"(?:как\s+меня\s+зовут|мо[её]\s+имя|my\s+name|what\s+is\s+my\s+name)",
    re.IGNORECASE | re.UNICODE,
)


def _connect() -> sqlite3.Connection:
    # connect_sqlite handles parent mkdir, WAL journal, busy timeout, Row factory.
    return connect_sqlite(DB_PATH)


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _normalize_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _project_row(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, name, path, active, created_at, updated_at FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_memory_schema(conn: sqlite3.Connection) -> None:
    columns = _column_names(conn, "memory")
    migrations = {
        "category": "ALTER TABLE memory ADD COLUMN category TEXT NOT NULL DEFAULT 'fact'",
        "source": "ALTER TABLE memory ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'",
        "importance": "ALTER TABLE memory ADD COLUMN importance INTEGER NOT NULL DEFAULT 5",
        "access_count": "ALTER TABLE memory ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0",
    }
    for column, sql in migrations.items():
        if column not in columns:
            conn.execute(sql)


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chats ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, "
            "pinned INTEGER NOT NULL DEFAULT 0, "
            "memory_saved INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "chat_id INTEGER NOT NULL, "
            "role TEXT NOT NULL, "
            "content TEXT NOT NULL, "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "context_window INTEGER NOT NULL DEFAULT 131072, "
            "default_model TEXT NOT NULL DEFAULT 'local-model', "
            "agent_profile TEXT NOT NULL DEFAULT 'default'"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS projects ("
            "id TEXT PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "path TEXT NOT NULL UNIQUE, "
            "active INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS memory ("
            "id TEXT PRIMARY KEY, "
            "text TEXT NOT NULL, "
            "category TEXT NOT NULL DEFAULT 'fact', "
            "source TEXT NOT NULL DEFAULT 'manual', "
            "importance INTEGER NOT NULL DEFAULT 5, "
            "access_count INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        _ensure_memory_schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO settings(id, context_window, default_model, agent_profile) "
            "VALUES(1, 16384, 'local-model', 'default')"
        )
        conn.commit()
    finally:
        conn.close()


def list_chats() -> list[dict[str, Any]]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, title, pinned, memory_saved, created_at, updated_at "
            "FROM chats ORDER BY pinned DESC, updated_at DESC, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def create_chat(title: str = "New chat") -> dict[str, Any]:
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO chats(title, pinned, memory_saved) VALUES (?, 0, 0)",
            ((title or "New chat").strip() or "New chat",),
        )
        row = conn.execute(
            "SELECT id, title, pinned, memory_saved, created_at, updated_at FROM chats WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def update_chat(
    chat_id: int,
    *,
    title: str | None = None,
    pinned: bool | None = None,
    memory_saved: bool | None = None,
) -> dict[str, Any] | None:
    init_db()
    conn = _connect()
    try:
        current = conn.execute(
            "SELECT id, title, pinned, memory_saved FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
        if current is None:
            return None
        next_title = current["title"] if title is None else ((title or "").strip() or "New chat")
        next_pinned = current["pinned"] if pinned is None else int(bool(pinned))
        next_memory_saved = current["memory_saved"] if memory_saved is None else int(bool(memory_saved))
        conn.execute(
            "UPDATE chats SET title = ?, pinned = ?, memory_saved = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (next_title, next_pinned, next_memory_saved, chat_id),
        )
        row = conn.execute(
            "SELECT id, title, pinned, memory_saved, created_at, updated_at FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
        conn.commit()
        return _row_dict(row)
    finally:
        conn.close()


def delete_chat(chat_id: int) -> None:
    init_db()
    conn = _connect()
    try:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        conn.commit()
    finally:
        conn.close()


def get_messages(chat_id: int) -> list[dict[str, Any]]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, chat_id, role, content, created_at FROM messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def add_message(chat_id: int | None, role: str, content: str) -> dict[str, Any]:
    init_db()
    if not chat_id:
        chat_id = int(create_chat()["id"])
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO messages(chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content),
        )
        conn.execute("UPDATE chats SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (chat_id,))
        row = conn.execute("SELECT id, chat_id, role, content, created_at FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        conn.commit()
        return {"chat_id": chat_id, "message": dict(row)}
    finally:
        conn.close()


def get_settings() -> dict[str, Any]:
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT context_window, default_model, agent_profile FROM settings WHERE id = 1"
        ).fetchone()
        result = dict(row) if row else {
            "context_window": 131072,
            "default_model": "local-model",
            "agent_profile": "default",
        }
        result["route_model_map"] = {}
        result["orchestration_enabled"] = False
        return result
    finally:
        conn.close()


def save_settings(context_window: int, default_model: str, agent_profile: str) -> dict[str, Any]:
    init_db()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE settings SET context_window = ?, default_model = ?, agent_profile = ? WHERE id = 1",
            (int(context_window), default_model or "local-model", agent_profile or "default"),
        )
        conn.commit()
    finally:
        conn.close()
    return get_settings()


def list_projects() -> list[dict[str, Any]]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, name, path, active, created_at, updated_at FROM projects "
            "ORDER BY active DESC, updated_at DESC, name ASC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def active_project() -> dict[str, Any]:
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, name, path, active, created_at, updated_at FROM projects WHERE active = 1 "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else {}
    finally:
        conn.close()


def upsert_project(path: str, name: str = "", *, active: bool = True) -> dict[str, Any]:
    init_db()
    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"project path does not exist or is not a directory: {root}")
    normalized = str(root)
    project_name = (name or root.name or "Chat Project").strip()
    conn = _connect()
    try:
        existing = conn.execute("SELECT id FROM projects WHERE path = ?", (normalized,)).fetchone()
        project_id = str(existing["id"]) if existing else uuid.uuid4().hex
        if active:
            conn.execute("UPDATE projects SET active = 0")
        conn.execute(
            "INSERT INTO projects(id, name, path, active) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET name = excluded.name, active = excluded.active, updated_at = CURRENT_TIMESTAMP",
            (project_id, project_name, normalized, int(bool(active))),
        )
        row = _project_row(conn, project_id)
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def set_active_project(project_id: str) -> dict[str, Any] | None:
    init_db()
    conn = _connect()
    try:
        row = _project_row(conn, project_id)
        if row is None:
            return None
        conn.execute("UPDATE projects SET active = 0")
        conn.execute("UPDATE projects SET active = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (project_id,))
        row = _project_row(conn, project_id)
        conn.commit()
        return _row_dict(row)
    finally:
        conn.close()


def remove_project(project_id: str) -> bool:
    init_db()
    conn = _connect()
    try:
        current = _project_row(conn, project_id)
        if current is None:
            return False
        was_active = bool(current["active"])
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        if was_active:
            next_row = conn.execute(
                "SELECT id FROM projects ORDER BY updated_at DESC, name ASC LIMIT 1"
            ).fetchone()
            if next_row is not None:
                conn.execute("UPDATE projects SET active = 1 WHERE id = ?", (next_row["id"],))
        conn.commit()
        return True
    finally:
        conn.close()


def clear_active_project() -> None:
    init_db()
    conn = _connect()
    try:
        conn.execute("UPDATE projects SET active = 0")
        conn.commit()
    finally:
        conn.close()


def _memory_row_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["importance"] = int(item.get("importance") or 5)
    item["access_count"] = int(item.get("access_count") or 0)
    return item


def _clean_memory_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    return cleaned.strip(" \t\r\n\"'`.,;:!?")


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD_RE.finditer(text or "") if len(match.group(0)) > 1}


def list_memory(limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    safe_limit = max(1, min(int(limit or 100), 500))
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, text, category, source, importance, access_count, created_at "
            "FROM memory ORDER BY created_at DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
        return [_memory_row_dict(row) for row in rows]
    finally:
        conn.close()


def memory_stats() -> dict[str, Any]:
    init_db()
    conn = _connect()
    try:
        total = int(conn.execute("SELECT COUNT(*) AS n FROM memory").fetchone()["n"])
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM memory GROUP BY category ORDER BY category ASC"
        ).fetchall()
        return {"total": total, "by_category": {str(row["category"]): int(row["n"]) for row in rows}}
    finally:
        conn.close()


def add_memory(
    text: str,
    *,
    category: str = "fact",
    source: str = "manual",
    importance: int = 5,
) -> dict[str, Any]:
    init_db()
    cleaned = _clean_memory_text(text)
    if len(cleaned) < 2:
        raise ValueError("memory text is too short")
    safe_category = (category or "fact").strip()[:40] or "fact"
    safe_source = (source or "manual").strip()[:40] or "manual"
    safe_importance = max(1, min(int(importance or 5), 10))
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id, text, category, source, importance, access_count, created_at "
            "FROM memory WHERE lower(text) = lower(?) LIMIT 1",
            (cleaned,),
        ).fetchone()
        if existing is not None:
            return _memory_row_dict(existing)
        memory_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO memory(id, text, category, source, importance) VALUES (?, ?, ?, ?, ?)",
            (memory_id, cleaned, safe_category, safe_source, safe_importance),
        )
        row = conn.execute(
            "SELECT id, text, category, source, importance, access_count, created_at FROM memory WHERE id = ?",
            (memory_id,),
        ).fetchone()
        conn.commit()
        return _memory_row_dict(row)
    finally:
        conn.close()


def delete_memory(memory_id: str) -> bool:
    init_db()
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM memory WHERE id = ?", (str(memory_id),))
        conn.commit()
        return (cur.rowcount or 0) > 0
    finally:
        conn.close()


def search_memory(query: str, limit: int = 8) -> list[dict[str, Any]]:
    init_db()
    safe_limit = max(1, min(int(limit or 8), 50))
    query_text = str(query or "").strip()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, text, category, source, importance, access_count, created_at "
            "FROM memory ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
        items = [_memory_row_dict(row) for row in rows]
        if not query_text:
            return items[:safe_limit]
        if _DIRECT_NAME_QUERY_RE.search(query_text):
            name_items = [item for item in items if str(item.get("text") or "").lower().startswith("user name:")]
            if name_items:
                _mark_memory_used([str(item["id"]) for item in name_items[:safe_limit]])
                return name_items[:safe_limit]
        query_tokens = _tokens(query_text)
        if not query_tokens:
            return items[:safe_limit]
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            text = str(item.get("text") or "")
            text_tokens = _tokens(text)
            score = len(query_tokens & text_tokens)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], str(pair[1].get("created_at") or "")), reverse=True)
        selected = [item for _score, item in scored[:safe_limit]]
        if selected:
            _mark_memory_used([str(item["id"]) for item in selected])
            return selected
        return []
    finally:
        conn.close()


def _mark_memory_used(memory_ids: list[str]) -> None:
    if not memory_ids:
        return
    conn = _connect()
    try:
        conn.executemany(
            "UPDATE memory SET access_count = access_count + 1 WHERE id = ?",
            [(item_id,) for item_id in memory_ids],
        )
        conn.commit()
    finally:
        conn.close()


def extract_memory_facts(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    facts: list[str] = []
    for pattern in _NAME_PATTERNS:
        match = pattern.search(raw)
        if match:
            name = _clean_memory_text(match.group(1))
            if name:
                facts.append(f"User name: {name}")
            break
    remember_match = _REMEMBER_RE.match(raw)
    if remember_match:
        remembered = _clean_memory_text(remember_match.group(1))
        if facts and any(pattern.search(remembered) for pattern in _NAME_PATTERNS):
            return facts
        if remembered and remembered not in facts:
            facts.append(remembered)
    return facts


def capture_memory_from_text(text: str) -> list[dict[str, Any]]:
    saved: list[dict[str, Any]] = []
    for fact in extract_memory_facts(text):
        saved.append(add_memory(fact, category="fact", source="chat", importance=8))
    return saved


def build_memory_context(query: str, limit: int = 8) -> str:
    items = search_memory(query, limit=limit)
    if not items:
        return ""
    lines = [
        "CHAT AGENT MEMORY CONTEXT:",
        "Use these saved facts when they are relevant. If the user asks about a saved fact, answer from this memory.",
    ]
    lines.extend(f"- {item['text']}" for item in items if str(item.get("text") or "").strip())
    return "\n".join(lines)
