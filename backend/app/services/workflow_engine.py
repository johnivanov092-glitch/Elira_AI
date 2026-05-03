"""Workflow Engine — compatibility shim (Agent OS Phase 4).

All logic lives in ``app.application.workflow_engine.runtime``.
Public API re-exported for all callers: main.py, routes, multi_agent_chain,
agents/core.
"""
from __future__ import annotations

from app.application.workflow_engine.runtime import (
    DB_PATH,  # noqa: F401 — used by Phase 4/5 test setUp/tearDown
    MULTI_AGENT_DEFAULT_WORKFLOW_ID,
    MULTI_AGENT_FULL_WORKFLOW_ID,
    MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID,
    MULTI_AGENT_REFLECTION_WORKFLOW_ID,
    cancel_workflow_run,
    create_workflow_template,
    delete_workflow_template,
    get_workflow_run,
    get_workflow_template,
    list_workflow_runs,
    list_workflow_templates,
    resume_workflow_run,
    run_legacy_multi_agent_workflow,
    run_multi_agent_workflow,
    seed_builtin_workflows,
    start_workflow_run,
    update_workflow_template,
)

__all__ = [
    "DB_PATH",
    "MULTI_AGENT_DEFAULT_WORKFLOW_ID",
    "MULTI_AGENT_FULL_WORKFLOW_ID",
    "MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID",
    "MULTI_AGENT_REFLECTION_WORKFLOW_ID",
    "cancel_workflow_run",
    "create_workflow_template",
    "delete_workflow_template",
    "get_workflow_run",
    "get_workflow_template",
    "list_workflow_runs",
    "list_workflow_templates",
    "resume_workflow_run",
    "run_legacy_multi_agent_workflow",
    "run_multi_agent_workflow",
    "seed_builtin_workflows",
    "start_workflow_run",
    "update_workflow_template",
]
