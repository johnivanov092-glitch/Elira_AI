from __future__ import annotations

from pathlib import Path
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
    permission_mode: str = "ask",
    workflow_approved: bool = False,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run a tool through the same providers used by the code-agent."""
    from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool
    from app.application.tool_providers.chat_builtin import chat_builtin_dispatch_fn
    from app.application.tool_providers import build_runtime_tool_registry
    from app.core.config import DATA_DIR

    root = Path(project_root).expanduser().resolve() if project_root else (DATA_DIR / "workspace").resolve()
    registry = build_runtime_tool_registry(root)

    def _dispatch(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name in registry.known_tools():
            return registry.dispatch_raw(name, payload)
        # Dynamic/plugin handlers still live in the existing database registry.
        return chat_builtin_dispatch_fn(name, payload)

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
            permission_mode=permission_mode,
            workflow_approved=workflow_approved,
        ),
        dispatch_fn=_dispatch,
    )
    return result.output
