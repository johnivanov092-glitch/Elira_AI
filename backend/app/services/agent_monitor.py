"""Agent Monitor — compatibility shim (Agent OS Phase 5).

All logic lives in ``app.application.monitoring.runtime``.
Public API re-exported for all callers: agents_service, agent_sandbox,
workflow_engine, routes.
"""
from __future__ import annotations

from app.application.monitoring.runtime import (
    DB_PATH,
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_MAX_EXECUTION_SECONDS,
    DEFAULT_MAX_RUNS_PER_HOUR,
    WORKFLOW_ENGINE_AGENT_ID,
    count_agent_runs_last_hour,
    ensure_agent_limit,
    get_agent_limit,
    get_agent_os_dashboard,
    get_agent_os_health,
    get_recent_blocked_runs,
    list_agent_limits,
    record_agent_run_metric,
    record_metric,
    record_resource_usage,
    record_sandbox_block,
    record_workflow_run_metric,
    record_workflow_step_metric,
    seed_default_limits,
    update_agent_limit,
)

__all__ = [
    "DB_PATH",
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "DEFAULT_MAX_EXECUTION_SECONDS",
    "DEFAULT_MAX_RUNS_PER_HOUR",
    "WORKFLOW_ENGINE_AGENT_ID",
    "count_agent_runs_last_hour",
    "ensure_agent_limit",
    "get_agent_limit",
    "get_agent_os_dashboard",
    "get_agent_os_health",
    "get_recent_blocked_runs",
    "list_agent_limits",
    "record_agent_run_metric",
    "record_metric",
    "record_resource_usage",
    "record_sandbox_block",
    "record_workflow_run_metric",
    "record_workflow_step_metric",
    "seed_default_limits",
    "update_agent_limit",
]
