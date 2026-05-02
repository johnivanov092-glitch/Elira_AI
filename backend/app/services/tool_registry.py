"""Tool Registry — compatibility shim (Agent OS Phase 2).

All logic lives in ``app.application.tool_registry.runtime``.
Public API re-exported for all callers: main.py, tool_service.py, routes.
"""
from __future__ import annotations

from app.application.tool_registry.runtime import (
    DB_PATH,
    delete_tool,
    execute_tool,
    get_tool,
    list_tools_with_schemas,
    register_tool,
    register_tool_from_dict,
    seed_builtin_tools,
    update_tool,
    validate_tool_args,
)

__all__ = [
    "DB_PATH",
    "delete_tool",
    "execute_tool",
    "get_tool",
    "list_tools_with_schemas",
    "register_tool",
    "register_tool_from_dict",
    "seed_builtin_tools",
    "update_tool",
    "validate_tool_args",
]
