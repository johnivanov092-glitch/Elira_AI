"""Stable ordering for the compact built-in capability core.

Runtime providers determine the actual available schemas. Capability loading
is prompt composition, not an allowlist, scope, or authorization boundary.
"""
from __future__ import annotations

# Stable order for the local built-in tools in prompts and compatibility APIs.
BASE_TOOLS: tuple[str, ...] = (
    "capability_load", "runtime_control",
    "read_file", "glob", "grep", "path_exists", "project_map",
    "todo_update", "delegate_task",
    "write_file", "edit_file", "run_bash",
)
# Legacy prompt grouping; persona never changes tool access.
READONLY_TOOLS: tuple[str, ...] = (
    "capability_load", "read_file", "glob", "grep", "path_exists", "project_map",
)
