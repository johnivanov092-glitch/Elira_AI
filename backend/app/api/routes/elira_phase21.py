"""Compatibility route aliases for the legacy phase21 module name."""
from __future__ import annotations

from app.api.routes.elira_execution_controller import (
    ExecutionControllerRunPayload as Phase21RunPayload,
    get_execution_controller_history as get_phase21_history,
    list_execution_controller_history as list_phase21_history,
    router,
    run_execution_controller as run_phase21,
)

__all__ = [
    "Phase21RunPayload",
    "get_phase21_history",
    "list_phase21_history",
    "router",
    "run_phase21",
]
