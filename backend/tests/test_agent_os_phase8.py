"""Tool registry execution events."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest


# ─── Fix 1: tool.executed event ────────────────────────────────────────────

def test_execute_tool_emits_tool_executed_event(tmp_path, monkeypatch):
    """execute_tool() must publish a tool.executed event after every call."""
    import app.application.tool_registry.runtime as reg
    import app.application.tool_registry.store as store_mod
    import app.application.event_bus.runtime as eb
    from app.infrastructure.db.connection import connect_sqlite

    # Isolated DBs
    db_file = tmp_path / "tool_registry.db"
    eb_db = tmp_path / "event_bus.db"

    monkeypatch.setattr(reg, "DB_PATH", db_file)
    orig_conn = reg._conn
    reg._conn = lambda: connect_sqlite(db_file)  # type: ignore[assignment]
    reg._init_db()

    monkeypatch.setattr(eb, "DB_PATH", eb_db)
    orig_eb_conn = eb._conn
    eb._conn = lambda: connect_sqlite(eb_db)  # type: ignore[assignment]
    eb._init_db()

    try:
        reg.register_tool(
            "test_ping_p8",
            lambda args: {"ok": True, "pong": True},
            display_name="Ping",
            category="testing",
        )

        result = reg.execute_tool("test_ping_p8", {})
        assert result.get("ok") is True

        events, total = eb.list_events(event_type="tool.executed")
        assert total >= 1
        payloads = [e["payload"] for e in events]
        assert any(
            p.get("tool_name") == "test_ping_p8" and p.get("success") is True
            for p in payloads
        )
    finally:
        reg._conn = orig_conn
        eb._conn = orig_eb_conn
        reg._handlers.pop("test_ping_p8", None)


def test_execute_tool_failed_call_emits_event_with_success_false(tmp_path, monkeypatch):
    """tool.executed event must have success=False when the handler raises."""
    import app.application.tool_registry.runtime as reg
    import app.application.event_bus.runtime as eb
    from app.infrastructure.db.connection import connect_sqlite

    db_file = tmp_path / "tool_registry_fail.db"
    eb_db = tmp_path / "event_bus_fail.db"

    monkeypatch.setattr(reg, "DB_PATH", db_file)
    orig_conn = reg._conn
    reg._conn = lambda: connect_sqlite(db_file)  # type: ignore[assignment]
    reg._init_db()

    monkeypatch.setattr(eb, "DB_PATH", eb_db)
    orig_eb_conn = eb._conn
    eb._conn = lambda: connect_sqlite(eb_db)  # type: ignore[assignment]
    eb._init_db()

    try:
        def _boom(args):
            raise RuntimeError("intentional failure")

        reg.register_tool("test_boom_p8", _boom, category="testing")
        result = reg.execute_tool("test_boom_p8", {})
        assert result.get("ok") is False

        events, total = eb.list_events(event_type="tool.executed")
        assert total >= 1
        payloads = [e["payload"] for e in events]
        assert any(
            p.get("tool_name") == "test_boom_p8" and p.get("success") is False
            for p in payloads
        )
    finally:
        reg._conn = orig_conn
        eb._conn = orig_eb_conn
        reg._handlers.pop("test_boom_p8", None)
