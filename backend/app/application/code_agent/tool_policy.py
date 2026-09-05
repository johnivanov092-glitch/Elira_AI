"""Default working tools and compatibility ordering.

Runtime providers determine the actual available schemas. Capability loading
is prompt composition, not an allowlist, scope, or authorization boundary.
"""
from __future__ import annotations

# The model gets ordinary work tools immediately. Keep specialist groups and
# external integrations discoverable through the same registry/executor.
BASE_TOOLS: tuple[str, ...] = (
    "capability_load", "runtime_control",
    "read_file", "write_file", "edit_file", "glob", "grep", "path_exists",
    "project_map", "todo_update", "delegate_task", "run_bash", "run_server",
    "web_search", "web_fetch", "recall", "remember",
)
# Legacy prompt grouping; persona never changes tool access.
READONLY_TOOLS: tuple[str, ...] = (
    "capability_load", "read_file", "glob", "grep", "path_exists", "project_map",
)
