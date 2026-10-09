"""Retired tools cannot execute or survive as stale app-owned inventory."""
from __future__ import annotations

import pytest

from app.application.code_agent.tool_schemas import build_tool_schemas
from app.application.tool_providers.builtin import BuiltinToolProvider
from app.application.tool_registry import runtime
from app.application.tool_registry.builtins import build_builtin_tools


@pytest.mark.parametrize("name", ["file_gen", "finance_calc", "computer"])
def test_retired_tool_is_absent_from_all_native_entry_points(tmp_path, name):
    assert name not in {item["function"]["name"] for item in build_tool_schemas()}
    assert name not in {item["name"] for item in build_builtin_tools()}
    provider = BuiltinToolProvider(tmp_path)
    assert provider.owns(name) is False
    result = provider.dispatch(name, {})
    assert result["ok"] is False and result["error"] == "unknown_tool"


@pytest.mark.parametrize("source", ["code_agent", "plugin", "user"])
def test_seed_removes_only_retired_app_rows_and_handlers(tmp_path, monkeypatch, source):
    monkeypatch.setattr(runtime, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr(runtime, "_handlers", {})
    monkeypatch.setattr(runtime, "_BUILTIN_SEEDED", False)
    runtime._init_db()
    for name in ("file_gen", "finance_calc", "computer"):
        runtime.register_tool(name, lambda _: {"ok": True}, source=source)
    runtime.register_tool("custom_processor", lambda _: {"ok": True}, source="code_agent")

    runtime.seed_builtin_tools()

    for name in ("file_gen", "finance_calc", "computer"):
        assert (runtime.get_tool(name) is None) == (source == "code_agent")
        assert (name not in runtime._handlers) == (source == "code_agent")
    assert runtime.get_tool("custom_processor") is not None
    assert runtime.get_tool("read_file") is not None
    assert runtime.seed_builtin_tools() == 0
