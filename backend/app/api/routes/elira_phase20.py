"""Compatibility route aliases for the legacy phase20 module name."""
from __future__ import annotations

from app.api.routes.elira_execution_loop import (
    ExecutionLoopRunPayload as Phase20RunPayload,
    get_execution_loop_history as get_phase20_history,
    list_execution_loop_history as list_phase20_history,
    router,
    run_execution_loop as run_phase20,
)

__all__ = [
    "Phase20RunPayload",
    "get_phase20_history",
    "list_phase20_history",
    "router",
    "run_phase20",
]
