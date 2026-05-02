from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.application.event_bus import store as event_bus_store
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
    event_bus_store.init_db(conn_factory=_conn, create_sql=_CREATE_SQL)


_init_db()


def _dumps(value: Any) -> str:
    return event_bus_store.dumps_json(value)


def _loads(raw: Any, default: Any) -> Any:
    return event_bus_store.loads_json(raw, default)


def _row_to_event(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return event_bus_store.row_to_event(loads_func=_loads, row=row)


def _row_to_message(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return event_bus_store.row_to_message(loads_func=_loads, row=row)


def _row_to_subscription(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return event_bus_store.row_to_subscription(row=row)


def get_event(event_id: str) -> dict[str, Any] | None:
    return event_bus_store.get_event(
        conn_factory=_conn,
        row_to_event_func=_row_to_event,
        event_id=event_id,
    )


def emit_event(
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source_agent_id: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    return event_bus_store.emit_event(
        conn_factory=_conn,
        now_func=_now,
        dumps_func=_dumps,
        get_event_func=get_event,
        event_type=event_type,
        payload=payload,
        source_agent_id=source_agent_id,
        event_id=event_id,
    )


def list_events(
    *,
    event_type: str | None = None,
    source_agent_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    return event_bus_store.list_events(
        conn_factory=_conn,
        row_to_event_func=_row_to_event,
        event_type=event_type,
        source_agent_id=source_agent_id,
        limit=limit,
        offset=offset,
    )


def get_message(message_id: str) -> dict[str, Any] | None:
    return event_bus_store.get_message(
        conn_factory=_conn,
        row_to_message_func=_row_to_message,
        message_id=message_id,
    )


def send_message(
    *,
    to_agent: str,
    content: Any,
    from_agent: str | None = None,
    reply_to: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    return event_bus_store.send_message(
        conn_factory=_conn,
        now_func=_now,
        dumps_func=_dumps,
        get_message_func=get_message,
        to_agent=to_agent,
        content=content,
        from_agent=from_agent,
        reply_to=reply_to,
        message_id=message_id,
    )


def get_agent_messages(
    agent_id: str,
    *,
    unread_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    return event_bus_store.get_agent_messages(
        conn_factory=_conn,
        row_to_message_func=_row_to_message,
        agent_id=agent_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )


def mark_message_read(message_id: str, read: bool = True) -> dict[str, Any] | None:
    return event_bus_store.mark_message_read(
        conn_factory=_conn,
        get_message_func=get_message,
        message_id=message_id,
        read=read,
    )


def get_subscription(subscriber_id: str, event_type: str) -> dict[str, Any] | None:
    return event_bus_store.get_subscription(
        conn_factory=_conn,
        row_to_subscription_func=_row_to_subscription,
        subscriber_id=subscriber_id,
        event_type=event_type,
    )


def subscribe(
    *,
    subscriber_id: str,
    event_type: str,
    handler_name: str | None = None,
) -> dict[str, Any]:
    return event_bus_store.subscribe(
        conn_factory=_conn,
        now_func=_now,
        get_subscription_func=get_subscription,
        subscriber_id=subscriber_id,
        event_type=event_type,
        handler_name=handler_name,
    )


def list_subscriptions(
    *,
    subscriber_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    return event_bus_store.list_subscriptions(
        conn_factory=_conn,
        row_to_subscription_func=_row_to_subscription,
        subscriber_id=subscriber_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )


def unsubscribe(subscriber_id: str, event_type: str) -> dict[str, Any]:
    return event_bus_store.unsubscribe(
        conn_factory=_conn,
        subscriber_id=subscriber_id,
        event_type=event_type,
    )
