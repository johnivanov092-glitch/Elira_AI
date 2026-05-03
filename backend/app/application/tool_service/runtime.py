# -*- coding: utf-8 -*-
"""Application-layer runtime for the Tool Service adapter.

Provides the thin result-formatting wrappers that sit between callers and the
raw Tool Registry / Smart Memory APIs.  Pure Python — no HTTP, no FastAPI.
"""
from __future__ import annotations

from typing import Any


def list_tools() -> dict[str, Any]:
    """Return all registered tools with JSON Schema, wrapped in an API envelope."""
    from app.application.tool_registry.runtime import list_tools_with_schemas

    tools = list_tools_with_schemas()
    return {"ok": True, "tools": tools, "count": len(tools)}


def search_memory_tool(profile: str, query: str, limit: int = 5) -> dict[str, Any]:
    """Search smart memory and attach the requested profile name to the result."""
    from app.application.smart_memory import search_memory as _search

    result = _search(query=query, limit=max(1, int(limit)))
    result["profile"] = str(profile or "default")
    return result


def run_tool(tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a registered tool by name via the Tool Registry."""
    from app.application.tool_registry.runtime import execute_tool

    result = execute_tool(tool_name, args)
    try:
        from app.application.event_bus.runtime import emit_event

        emit_event(
            event_type="tool.executed",
            payload={
                "tool_name": tool_name,
                "ok": bool(result.get("ok")),
                "args_keys": list((args or {}).keys()),
            },
            source_agent_id=None,
        )
    except Exception:
        pass
    return result
