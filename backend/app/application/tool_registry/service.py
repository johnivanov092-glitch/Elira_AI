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


def run_tool(
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    agent_id: str = "chat",
    run_id: str = "",
    project_scope_id: str = "",
    source: str = "chat",
    workflow_id: str = "",
    step_id: str = "",
) -> dict[str, Any]:
    """Thin wrapper over the unified ToolExecutor for chat and workflow callers."""
    from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool
    from app.application.tool_providers.chat_builtin import chat_builtin_dispatch_fn

    result = execute_tool(
        ToolExecutionRequest(
            run_id=run_id,
            agent_id=agent_id,
            project_scope_id=project_scope_id,
            tool_name=tool_name,
            args=args or {},
            source=source,
            workflow_id=workflow_id,
            step_id=step_id,
        ),
        dispatch_fn=chat_builtin_dispatch_fn,
    )
    return result.output
