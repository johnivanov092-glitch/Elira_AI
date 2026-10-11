"""Default working tools and compatibility ordering.

Runtime providers determine the actual available schemas. Capability loading
is prompt composition, not an allowlist, scope, or authorization boundary.
"""
from __future__ import annotations

# The model gets ordinary work tools immediately. Keep specialist groups and
# external integrations discoverable through the same registry/executor.
BASE_TOOLS: tuple[str, ...] = (
    "capability_load",
    "read_file", "write_file", "edit_file", "glob", "grep",
    "run_bash", "run_server", "todo_update",
    # Exact arithmetic stays one call: through write_file + run_bash it took 6-8
    # steps, and in its head the model got 10 of 30 sums right (2026-10-11).
    "math",
)
