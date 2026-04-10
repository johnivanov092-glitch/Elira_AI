from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite


DB_PATH: Path = sqlite_data_file("event_bus.db")

SUPPORTED_EVENT_TYPES = (
    "agent.run.started",
    "agent.run.completed",
    "agent.limit.updated",
    "sandbox.policy.blocked",
    "tool.executed",
    "workflow.run.started",
    "workflow.run.paused",
    "workflow.run.resumed",
    "workflow.run.completed",
    "workflow.run.cancelled",
    "workflow.step.started",
    "workflow.step.completed",
    "workflow.step.failed",
)

# TODO: wire tool.executed after Phase 2 merge.

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    source_agent_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_bus_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_event_bus_events_source ON events(source_agent_id);
CREATE INDEX IF NOT EXISTS idx_event_bus_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    from_agent TEXT NOT NULL DEFAULT '',
    to_agent TEXT NOT NULL,
    content_json TEXT NOT NULL DEFAULT '{}',
    reply_to TEXT NOT NULL DEFAULT '',
    read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_bus_messages_target_read ON agent_messages(to_agent, read);
CREATE INDEX IF NOT EXISTS idx_event_bus_messages_created ON agent_messages(created_at);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscriber_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    handler_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(subscriber_id, event_type)
);
CREATE INDEX IF NOT EXISTS idx_event_bus_subscriber ON subscriptions(subscriber_id);
CREATE INDEX IF NOT EXISTS idx_event_bus_subscription_type ON subscriptions(event_type);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH)


def _init_db() -> None:
    with _conn() as con:
        con.executescript(_CREATE_SQL)


_init_db()


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _row_to_event(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["payload"] = _loads(data.pop("payload_json", "{}"), {})
    return data


def _row_to_message(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["content"] = _loads(data.pop("content_json", "{}"), {})
    data["read"] = bool(data.get("read"))
    return data


def _row_to_subscription(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def get_event(event_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
    return _row_to_event(row)


def emit_event(
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source_agent_id: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    event_type = str(event_type or "").strip()
    if not event_type:
        raise ValueError("event_type is required")

    actual_event_id = str(event_id or f"evt-{uuid.uuid4().hex}")
    created_at = _now()

    with _conn() as con:
        con.execute(
            """
            INSERT INTO events (event_id, event_type, payload_json, source_agent_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                event_type = excluded.event_type,
                payload_json = excluded.payload_json,
                source_agent_id = excluded.source_agent_id,
                created_at = excluded.created_at
            """,
            (
                actual_event_id,
                event_type,
                _dumps(payload or {}),
                str(source_agent_id or ""),
                created_at,
            ),
        )

    return get_event(actual_event_id) or {}


def list_events(
    *,
    event_type: str | None = None,
    source_agent_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: list[Any] = []

    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if source_agent_id:
        clauses.append("source_agent_id = ?")
        params.append(source_agent_id)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _conn() as con:
        total_row = con.execute(f"SELECT COUNT(*) AS cnt FROM events {where}", params).fetchone()
        rows = con.execute(
            f"""
            SELECT * FROM events {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, max(1, int(limit)), max(0, int(offset))],
        ).fetchall()

    total = int(total_row["cnt"]) if total_row else 0
    events = [_row_to_event(row) for row in rows]
    return [event for event in events if event], total


def get_message(message_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM agent_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
    return _row_to_message(row)


def send_message(
    *,
    to_agent: str,
    content: Any,
    from_agent: str | None = None,
    reply_to: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    to_agent = str(to_agent or "").strip()
    if not to_agent:
        raise ValueError("to_agent is required")

    actual_message_id = str(message_id or f"msg-{uuid.uuid4().hex}")
    created_at = _now()

    with _conn() as con:
        con.execute(
            """
            INSERT INTO agent_messages
                (message_id, from_agent, to_agent, content_json, reply_to, read, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                from_agent = excluded.from_agent,
                to_agent = excluded.to_agent,
                content_json = excluded.content_json,
                reply_to = excluded.reply_to
            """,
            (
                actual_message_id,
                str(from_agent or ""),
                to_agent,
                _dumps(content),
                str(reply_to or ""),
                0,
                created_at,
            ),
        )

    return get_message(actual_message_id) or {}


def get_agent_messages(
    agent_id: str,
    *,
    unread_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clauses = ["to_agent = ?"]
    params: list[Any] = [agent_id]
    if unread_only:
        clauses.append("read = 0")
    where = f"WHERE {' AND '.join(clauses)}"

    with _conn() as con:
        total_row = con.execute(
            f"SELECT COUNT(*) AS cnt FROM agent_messages {where}",
            params,
        ).fetchone()
        rows = con.execute(
            f"""
            SELECT * FROM agent_messages {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, max(1, int(limit)), max(0, int(offset))],
        ).fetchall()

    total = int(total_row["cnt"]) if total_row else 0
    messages = [_row_to_message(row) for row in rows]
    return [message for message in messages if message], total


def mark_message_read(message_id: str, read: bool = True) -> dict[str, Any] | None:
    with _conn() as con:
        cursor = con.execute(
            "UPDATE agent_messages SET read = ? WHERE message_id = ?",
            (1 if read else 0, message_id),
        )
        if cursor.rowcount <= 0:
            return None

    return get_message(message_id)


def get_subscription(subscriber_id: str, event_type: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute(
            """
            SELECT * FROM subscriptions
            WHERE subscriber_id = ? AND event_type = ?
            """,
            (subscriber_id, event_type),
        ).fetchone()
    return _row_to_subscription(row)


def subscribe(
    *,
    subscriber_id: str,
    event_type: str,
    handler_name: str | None = None,
) -> dict[str, Any]:
    subscriber_id = str(subscriber_id or "").strip()
    event_type = str(event_type or "").strip()
    if not subscriber_id:
        raise ValueError("subscriber_id is required")
    if not event_type:
        raise ValueError("event_type is required")

    created_at = _now()
    with _conn() as con:
        con.execute(
            """
            INSERT INTO subscriptions (subscriber_id, event_type, handler_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(subscriber_id, event_type) DO UPDATE SET
                handler_name = excluded.handler_name
            """,
            (subscriber_id, event_type, str(handler_name or ""), created_at),
        )

    return get_subscription(subscriber_id, event_type) or {}


def list_subscriptions(
    *,
    subscriber_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: list[Any] = []

    if subscriber_id:
        clauses.append("subscriber_id = ?")
        params.append(subscriber_id)
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _conn() as con:
        total_row = con.execute(f"SELECT COUNT(*) AS cnt FROM subscriptions {where}", params).fetchone()
        rows = con.execute(
            f"""
            SELECT * FROM subscriptions {where}
            ORDER BY subscriber_id, event_type
            LIMIT ? OFFSET ?
            """,
            [*params, max(1, int(limit)), max(0, int(offset))],
        ).fetchall()

    total = int(total_row["cnt"]) if total_row else 0
    subscriptions = [_row_to_subscription(row) for row in rows]
    return [subscription for subscription in subscriptions if subscription], total


def unsubscribe(subscriber_id: str, event_type: str) -> dict[str, Any]:
    with _conn() as con:
        cursor = con.execute(
            """
            DELETE FROM subscriptions
            WHERE subscriber_id = ? AND event_type = ?
            """,
            (subscriber_id, event_type),
        )
    return {
        "subscriber_id": subscriber_id,
        "event_type": event_type,
        "removed": cursor.rowcount > 0,
    }
