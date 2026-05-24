"""Compatibility route aliases for the legacy phase19 module name."""
from __future__ import annotations

from app.api.routes.elira_multi_file_loop import (
    MultiFileLoopRunPayload as Phase19RunPayload,
    get_multi_file_loop_history as get_phase19_history,
    list_multi_file_loop_history as list_phase19_history,
    router,
    run_multi_file_loop as run_phase19,
)

__all__ = [
    "Phase19RunPayload",
    "get_phase19_history",
    "list_phase19_history",
    "router",
    "run_phase19",
]
