"""Stable ordering for the compact built-in capability core.

Runtime providers determine the actual available schemas. Capability loading
is prompt composition, not an allowlist, scope, or authorization boundary.
"""
from __future__ import annotations

from app.application.code_agent.capabilities import CORE_BUILTIN_TOOL_ORDER

# Stable order for the local built-in tools in prompts and compatibility APIs.
BASE_TOOLS: tuple[str, ...] = CORE_BUILTIN_TOOL_ORDER
# Legacy prompt grouping; persona never changes tool access.
READONLY_TOOLS: tuple[str, ...] = (
    "capability_load", "read_file", "glob", "grep", "path_exists", "project_map",
)
