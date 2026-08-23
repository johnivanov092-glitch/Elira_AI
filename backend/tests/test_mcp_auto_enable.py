"""Live MCP tools mirror provider lifecycle without a policy gate."""
from __future__ import annotations

from unittest import mock

from app.application.tool_providers import mcp_provider


class _FakeProvider:
    def __init__(self, names: list[str]):
        self._names = names

    def get_schemas(self):
        return [
            {"function": {"name": name, "description": "", "parameters": {}}}
            for name in self._names
        ]


def _run_sync(registry_rows: list[dict], live_names: list[str]):
    updates: list[tuple[str, dict]] = []
    with mock.patch(
        "app.application.tool_registry.runtime.register_dynamic_tool"
    ) as register, mock.patch(
        "app.application.tool_registry.runtime.list_tools_with_schemas",
        return_value=registry_rows,
    ), mock.patch(
        "app.application.tool_registry.runtime.update_tool",
        side_effect=lambda name, payload: updates.append((name, payload)),
    ):
        mcp_provider.sync_mcp_tool_specs([_FakeProvider(live_names)])
    return register, dict(updates)


def test_live_tool_is_registered_and_reenabled() -> None:
    register, updates = _run_sync(
        [{"name": "srv__query", "enabled": False}],
        ["srv__query"],
    )
    register.assert_called_once()
    assert updates["srv__query"] == {"enabled": True}


def test_stale_tool_is_disabled_as_runtime_inventory() -> None:
    _register, updates = _run_sync(
        [{"name": "srv__gone", "enabled": True}],
        [],
    )
    assert updates["srv__gone"] == {"enabled": False}
