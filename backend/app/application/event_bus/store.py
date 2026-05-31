from __future__ import annotations

import json
import uuid
from typing import Any, Callable


def init_db(*, conn_factory: Callable[[], Any], create_sql: str) -> None:
    with conn_factory() as con:
        con.executescript(create_sql)


def dumps_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def loads_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def row_to_event(*, loads_func: Callable[[Any, Any], Any], row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["payload"] = loads_func(data.pop("payload_json", "{}"), {})
    return data


def row_to_message(*, loads_func: Callable[[Any, Any], Any], row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["content"] = loads_func(data.pop("content_json", "{}"), {})
    data["read"] = bool(data.get("read"))
    return data


def row_to_subscription(*, row: Any) -> dict[str, Any] | None:
    return dict(row) if row else None


def get_event(
    *,
    conn_factory: Callable[[], Any],
    row_to_event_func: Callable[[Any], dict[str, Any] | None],
    event_id: str,
) -> dict[str, Any] | None:
    with conn_factory() as con:
        row = con.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
    return row_to_event_func(row)


def emit_event(
    *,
    conn_factory: Callable[[], Any],
    now_func: Callable[[], str],
    dumps_func: Callable[[Any], str],
    get_event_func: Callable[[str], dict[str, Any] | None],
    event_type: str,
    payload: dict[str, Any] | None = None,
    source_agent_id: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    event_type = str(event_type or "").strip()
    if not event_type:
        raise ValueError("event_type is required")

    actual_event_id = str(event_id or f"evt-{uuid.uuid4().hex}")
    created_at = now_func()

    with conn_factory() as con:
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
                dumps_func(payload or {}),
                str(source_agent_id or ""),
                created_at,
            ),
        )

    return get_event_func(actual_event_id) or {}


def list_events(
    *,
    conn_factory: Callable[[], Any],
    row_to_event_func: Callable[[Any], dict[str, Any] | None],
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

    with conn_factory() as con:
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
    events = [row_to_event_func(row) for row in rows]
    return [event for event in events if event], total


def get_message(
    *,
    conn_factory: Callable[[], Any],
    row_to_message_func: Callable[[Any], dict[str, Any] | None],
    message_id: str,
) -> dict[str, Any] | None:
    with conn_factory() as con:
        row = con.execute(
            "SELECT * FROM agent_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
    return row_to_message_func(row)


def send_message(
    *,
    conn_factory: Callable[[], Any],
    now_func: Callable[[], str],
    dumps_func: Callable[[Any], str],
    get_message_func: Callable[[str], dict[str, Any] | None],
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
    created_at = now_func()

    with conn_factory() as con:
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
                dumps_func(content),
                str(reply_to or ""),
                0,
                created_at,
            ),
        )

    return get_message_func(actual_message_id) or {}


def get_agent_messages(
    *,
    conn_factory: Callable[[], Any],
    row_to_message_func: Callable[[Any], dict[str, Any] | None],
    agent_id: str,
    unread_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clauses = ["to_agent = ?"]
    params: list[Any] = [agent_id]
    if unread_only:
        clauses.append("read = 0")
    where = f"WHERE {' AND '.join(clauses)}"

    with conn_factory() as con:
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
    messages = [row_to_message_func(row) for row in rows]
    return [message for message in messages if message], total


def mark_message_read(
    *,
    conn_factory: Callable[[], Any],
    get_message_func: Callable[[str], dict[str, Any] | None],
    message_id: str,
    read: bool = True,
) -> dict[str, Any] | None:
    with conn_factory() as con:
        cursor = con.execute(
            "UPDATE agent_messages SET read = ? WHERE message_id = ?",
            (1 if read else 0, message_id),
        )
        if cursor.rowcount <= 0:
            return None

    return get_message_func(message_id)


def get_subscription(
    *,
    conn_factory: Callable[[], Any],
    row_to_subscription_func: Callable[[Any], dict[str, Any] | None],
    subscriber_id: str,
    event_type: str,
) -> dict[str, Any] | None:
    with conn_factory() as con:
        row = con.execute(
            """
            SELECT * FROM subscriptions
            WHERE subscriber_id = ? AND event_type = ?
            """,
            (subscriber_id, event_type),
        ).fetchone()
    return row_to_subscription_func(row)


def subscribe(
    *,
    conn_factory: Callable[[], Any],
    now_func: Callable[[], str],
    get_subscription_func: Callable[[str, str], dict[str, Any] | None],
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

    created_at = now_func()
    with conn_factory() as con:
        con.execute(
            """
            INSERT INTO subscriptions (subscriber_id, event_type, handler_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(subscriber_id, event_type) DO UPDATE SET
                handler_name = excluded.handler_name
            """,
            (subscriber_id, event_type, str(handler_name or ""), created_at),
        )

    return get_subscription_func(subscriber_id, event_type) or {}


def list_subscriptions(
    *,
    conn_factory: Callable[[], Any],
    row_to_subscription_func: Callable[[Any], dict[str, Any] | None],
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

    with conn_factory() as con:
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
    subscriptions = [row_to_subscription_func(row) for row in rows]
    return [subscription for subscription in subscriptions if subscription], total


def unsubscribe(
    *,
    conn_factory: Callable[[], Any],
    subscriber_id: str,
    event_type: str,
) -> dict[str, Any]:
    with conn_factory() as con:
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

