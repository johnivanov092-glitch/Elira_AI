"""Читающие MCP-инструменты (readOnlyHint=true) в режиме ask — без запроса одобрения (решение Джона 2026-10-05).

Повод: Atlas подключён к Elira только для чтения, а каждый вызов atlas_overview требовал нажатия.
"""
from __future__ import annotations

from unittest import mock

import pytest

from app.application.agent_kernel import impact_policy
from app.application.agent_kernel.executor import ToolExecutionRequest, permission_mode_auto_approves
from app.application.tool_providers import mcp_provider

ATLAS_TOOLS = [
    {"name": "atlas_overview", "description": "Map of the project.", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "atlas_file", "description": "File passport.", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "atlas_try", "description": "Run a task in the sandbox.", "inputSchema": {"type": "object"}},
    {"name": "atlas_decision_add", "description": "Record a decision.", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": False}},
]


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(impact_policy._MCP_READ_ONLY_TOOLS)
    impact_policy._MCP_READ_ONLY_TOOLS.clear()
    yield
    impact_policy._MCP_READ_ONLY_TOOLS.clear()
    impact_policy._MCP_READ_ONLY_TOOLS.update(saved)


def _request(tool: str, mode: str = "ask") -> ToolExecutionRequest:
    return ToolExecutionRequest(run_id="r", agent_id="code-agent", project_scope_id="p", tool_name=tool,
                                args={}, source="code_agent", permission_mode=mode)


def _load_atlas_provider():
    client = mock.Mock()
    client.list_tools.return_value = ATLAS_TOOLS
    provider = mcp_provider.McpToolProvider("atlas")
    with mock.patch.object(mcp_provider, "get_live_client", return_value=client):
        provider.get_schemas()
    return provider


def test_provider_records_only_tools_the_server_marks_read_only():
    _load_atlas_provider()
    assert impact_policy._MCP_READ_ONLY_TOOLS["atlas"] == {"atlas__atlas_overview", "atlas__atlas_file"}
    assert impact_policy.tool_call_is_change("atlas__atlas_overview", {}) is False
    assert impact_policy.tool_call_is_change("atlas__atlas_try", {}) is True  # без пометки — изменение
    assert impact_policy.tool_call_is_change("atlas__atlas_decision_add", {}) is True  # readOnlyHint=false


def test_ask_mode_runs_read_only_mcp_and_still_asks_for_the_rest():
    _load_atlas_provider()
    assert permission_mode_auto_approves(_request("atlas__atlas_overview")) is True
    assert permission_mode_auto_approves(_request("atlas__atlas_try")) is False
    assert permission_mode_auto_approves(_request("atlas__atlas_decision_add", "accept_edits")) is False
    # Без сведений от сервера (не запущен / не прочитан список) — по-прежнему спрашиваем.
    assert permission_mode_auto_approves(_request("other__read_thing")) is False


def test_stopped_server_forgets_its_read_only_tools():
    provider = _load_atlas_provider()
    with mock.patch.object(mcp_provider, "get_live_client", return_value=None):
        provider._refresh_schemas()
    assert impact_policy._MCP_READ_ONLY_TOOLS["atlas"] == frozenset()
    assert permission_mode_auto_approves(_request("atlas__atlas_overview")) is False
