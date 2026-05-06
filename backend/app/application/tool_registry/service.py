from __future__ import annotations

from typing import Any


def list_tools() -> dict[str, Any]:
    from app.application.tool_registry.runtime import list_tools_with_schemas

    tools = list_tools_with_schemas()
    return {"ok": True, "tools": tools, "count": len(tools)}


def search_memory_tool(profile: str, query: str, limit: int = 5) -> dict[str, Any]:
    from app.application.smart_memory import search_memory as smart_search_memory

    result = smart_search_memory(query=query, limit=max(1, int(limit)))
    result["profile"] = str(profile or "default")
    return result


def run_tool(tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.application.tool_registry.runtime import execute_tool

    return execute_tool(tool_name, args)
