"""Stable prompt ordering for the unified agent runtime.

Runtime providers determine the actual available schemas. These tuples are not
an allowlist, scope, activation layer, or authorization boundary.
"""
from __future__ import annotations

# Stable order for the local built-in tools in prompts and compatibility APIs.
BASE_TOOLS: tuple[str, ...] = (
    "read_file", "glob", "grep", "project_map", "recall", "remember",
    "todo_update", "delegate_task",
    "runtime_control",
    "write_file", "edit_file", "run_bash", "run_server",
    "web_search", "web_fetch", "http_api",
)
# Legacy prompt grouping; persona never changes tool access.
READONLY_TOOLS: tuple[str, ...] = ("read_file", "glob", "grep", "recall")
