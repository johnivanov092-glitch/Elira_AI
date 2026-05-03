"""Event Bus — compatibility shim (Agent OS Phase 3).

All logic lives in ``app.application.event_bus.runtime``.
Public API re-exported for all callers: agents_service, workflow_engine,
agent_monitor, routes.
"""
from __future__ import annotations

from app.application.event_bus.runtime import (
    DB_PATH,
    SUPPORTED_EVENT_TYPES,
    _init_db,  # noqa: F401 — used by Phase 3 test tearDown
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

__all__ = [
    "DB_PATH",
    "SUPPORTED_EVENT_TYPES",
    "_init_db",
    "emit_event",
    "get_agent_messages",
    "get_event",
    "get_message",
    "get_subscription",
    "list_events",
    "list_subscriptions",
    "mark_message_read",
    "send_message",
    "subscribe",
    "unsubscribe",
]
