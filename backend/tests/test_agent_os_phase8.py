"""
Phase 8 TODO fixes:
  1. tool.executed event emitted by execute_tool()
  2. context_builder uses app.application.advanced.runtime._project_path (not API layer)
"""
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


# ─── Fix 2: context_builder import direction ───────────────────────────────

def test_context_builder_does_not_import_from_api_routes():
    """_build_project_context_from_open_project must not import from app.api.routes.*"""
    import inspect
    import app.application.chat.context_builder as cb

    source = inspect.getsource(cb._build_project_context_from_open_project)
    assert "app.api.routes" not in source, (
        "_build_project_context_from_open_project still imports from app.api.routes — "
        "this is a wrong-direction dependency (application -> api)."
    )
    assert "app.application.advanced.runtime" in source, (
        "_project_path should now be imported from app.application.advanced.runtime"
    )


def test_context_builder_resolves_project_path(tmp_path):
    """_build_project_context_from_open_project must read _project_path from runtime."""
    import app.application.advanced.runtime as proj_rt
    import app.application.chat.context_builder as cb

    original = proj_rt._project_path
    try:
        proj_rt._project_path = str(tmp_path)
        (tmp_path / "hello.txt").write_text("hi")

        result = cb._build_project_context_from_open_project()
        assert tmp_path.name in result
        assert "hello.txt" in result
    finally:
        proj_rt._project_path = original