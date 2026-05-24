"""Compatibility route aliases for the legacy phase20 state module name."""
from __future__ import annotations

from app.api.routes.elira_execution_state import (
    ExecutionStatePayload as Phase20StatePayload,
    build_execution_state,
    list_execution_states,
    router,
)

__all__ = [
    "Phase20StatePayload",
    "build_execution_state",
    "list_execution_states",
    "router",
]
