"""ChatBuiltinToolProvider — wraps tool_registry database handlers.

Makes the tool_registry builtin tools visible to the unified ToolExecutor
via the standard ToolProvider dispatch protocol.  Calling _execute_raw
(not execute_tool) avoids double event emission — the executor owns the audit.
"""
from __future__ import annotations

import json
from typing import Any


class ChatBuiltinToolProvider:
    """Dispatch layer for tools registered in tool_registry.db."""

    name = "chat_builtin"

    def is_enabled(self) -> bool:
        return True

    def get_schemas(self) -> list[dict[str, Any]]:
        from app.application.tool_registry.runtime import list_tools_with_schemas
        tools = list_tools_with_schemas()
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters_schema") or {},
                },
            }
            for t in tools
        ]

    def owns(self, tool_name: str) -> bool:
        from app.application.tool_registry.runtime import get_tool
        return get_tool(tool_name) is not None

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        from app.application.tool_registry.runtime import _execute_raw
        result = _execute_raw(tool_name, args)
        if not isinstance(result, dict):
            return {"text": str(result)}
        if "text" not in result:
            result = {**result, "text": json.dumps(result, ensure_ascii=False)}
        return result


def chat_builtin_dispatch_fn(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Convenience callable for executor injection."""
    return ChatBuiltinToolProvider().dispatch(tool_name, args)
