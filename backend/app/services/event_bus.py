"""Thin facade — all event bus logic lives in application/event_bus.py.

Mutable module-level state (DB_PATH) lives in application/event_bus.py.
Tests that need to redirect the database path must import that module directly:

    import app.application.event_bus as _eb
    _eb.DB_PATH = Path(tmpdir) / "event_bus.db"
    _eb._init_db()
"""
from app.application.event_bus import (  # noqa: F401
    DB_PATH,
    SUPPORTED_EVENT_TYPES,
    _CREATE_SQL,
    _conn,
    _dumps,
    _init_db,
    _loads,
    _now,
    _row_to_event,
    _row_to_message,
    _row_to_subscription,
    emit_event,
    get_agent_messages,
    get_event,
    get_message,
    get_subscription,
    list_events,
    list_subscriptions,
    mark_message_read,
    send_message,
    subscribe,
    unsubscribe,
)
