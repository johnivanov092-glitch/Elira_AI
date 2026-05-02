"""Agent Registry — compatibility shim (Agent OS Phase 1).

All logic lives in ``app.application.agent_registry.runtime``.
Public API re-exported for all callers.
"""
from __future__ import annotations

from app.application.agent_registry.runtime import (
    DB_PATH,
    delete_agent,
    get_agent,
    get_agent_runs,
    get_agent_state,
    list_agents,
    record_agent_run,
    register_agent,
    resolve_agent,
    seed_builtin_agents,
    set_agent_state,
    update_agent,
)

__all__ = [
    "DB_PATH",
    "delete_agent",
    "get_agent",
    "get_agent_runs",
    "get_agent_state",
    "list_agents",
    "record_agent_run",
    "register_agent",
    "resolve_agent",
    "seed_builtin_agents",
    "set_agent_state",
    "update_agent",
]
