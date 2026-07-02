from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.application.monitoring import reporting as monitoring_reporting
from app.application.monitoring import store as monitoring_store
from app.application.workflows.db_path import get_workflow_db_path
from app.core.data_files import sqlite_data_file


DB_PATH: Path = sqlite_data_file("agent_monitor.db")

DEFAULT_MAX_RUNS_PER_HOUR = monitoring_store.DEFAULT_MAX_RUNS_PER_HOUR
DEFAULT_MAX_EXECUTION_SECONDS = monitoring_store.DEFAULT_MAX_EXECUTION_SECONDS
DEFAULT_MAX_CONTEXT_TOKENS = monitoring_store.DEFAULT_MAX_CONTEXT_TOKENS
WORKFLOW_ENGINE_AGENT_ID = monitoring_store.DEFAULT_WORKFLOW_ENGINE_AGENT_ID
_LIMIT_SEED_DONE = False


def _init_db() -> None:
    monitoring_store.init_db(DB_PATH)
    monitoring_store.migrate_memory_candidates_table(DB_PATH)
    monitoring_store.migrate_model_profiles_table(DB_PATH)
    monitoring_store.migrate_approval_args_sha256(DB_PATH)
    monitoring_store.migrate_agent_limits_columns(DB_PATH)
    monitoring_store.migrate_default_runtime_limits(DB_PATH)
    monitoring_store.migrate_normalize_full_tool_allowlists(DB_PATH)


_init_db()


def prune_metrics(max_age_days: int = 45) -> dict[str, Any]:
    """Retention for agent_monitor.db — delete telemetry older than max_age_days
    and VACUUM. Called on a daily cadence from the task-recovery scheduler so the
    DB stops growing monotonically."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    return monitoring_store.prune_old_metrics(DB_PATH, cutoff_iso=cutoff)


def seed_default_limits() -> int:
    global _LIMIT_SEED_DONE
    if _LIMIT_SEED_DONE:
        return 0

    created = 0
    agent_ids: list[str] = []
    loaded_registry = False
    try:
        from app.application.agent_registry.runtime import list_agents, seed_builtin_agents

        seed_builtin_agents()
        agent_ids = [
            str(item.get("id", "")).strip()
            for item in list_agents(enabled_only=False)
        ]
        loaded_registry = True
    except Exception:
        agent_ids = []

    if WORKFLOW_ENGINE_AGENT_ID not in agent_ids:
        agent_ids.append(WORKFLOW_ENGINE_AGENT_ID)

    if loaded_registry:
        monitoring_store.delete_unknown_builtin_limits(DB_PATH, set(agent_ids))

    for agent_id in agent_ids:
        if not agent_id:
            continue
        if monitoring_store.get_agent_limit(DB_PATH, agent_id):
            continue
        monitoring_store.upsert_limit(
            DB_PATH,
            monitoring_store.default_limit_payload(agent_id),
        )
        created += 1

    _LIMIT_SEED_DONE = True
    return created


def list_agent_limits() -> list[dict[str, Any]]:
    seed_default_limits()
    return monitoring_store.list_agent_limits(DB_PATH)


def get_agent_limit(agent_id: str) -> dict[str, Any] | None:
    return monitoring_store.get_agent_limit(DB_PATH, agent_id)


def ensure_agent_limit(agent_id: str) -> dict[str, Any]:
    seed_default_limits()
    existing = get_agent_limit(agent_id)
    if existing:
        return existing
    return monitoring_store.upsert_limit(
        DB_PATH,
        monitoring_store.default_limit_payload(agent_id),
    )


def update_agent_limit(agent_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    current = ensure_agent_limit(agent_id)
    merged = {
        "agent_id": agent_id,
        "max_runs_per_hour": int(
            updates.get(
                "max_runs_per_hour",
                current.get("max_runs_per_hour", DEFAULT_MAX_RUNS_PER_HOUR),
            )
        ),
        "max_execution_seconds": int(
            updates.get(
                "max_execution_seconds",
                current.get(
                    "max_execution_seconds",
                    DEFAULT_MAX_EXECUTION_SECONDS,
                ),
            )
        ),
        "max_context_tokens": int(
            updates.get(
                "max_context_tokens",
                current.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS),
            )
        ),
        "allowed_tools": list(updates.get("allowed_tools", current.get("allowed_tools", []))),
        "allowed_scopes": list(updates.get("allowed_scopes", current.get("allowed_scopes", []))),
    }
    updated = monitoring_store.upsert_limit(DB_PATH, merged)
    try:
        from app.application.event_bus.runtime import emit_event

        emit_event(
            event_type="agent.limit.updated",
            source_agent_id=agent_id,
            payload={
                "agent_id": agent_id,
                "max_runs_per_hour": updated.get(
                    "max_runs_per_hour",
                    DEFAULT_MAX_RUNS_PER_HOUR,
                ),
                "max_execution_seconds": updated.get(
                    "max_execution_seconds",
                    DEFAULT_MAX_EXECUTION_SECONDS,
                ),
                "max_context_tokens": updated.get(
                    "max_context_tokens",
                    DEFAULT_MAX_CONTEXT_TOKENS,
                ),
                "allowed_tools": updated.get("allowed_tools", []),
            },
        )
    except Exception:
        pass
    record_metric(
        metric_type="agent.limit.updated",
        agent_id=agent_id,
        ok=True,
        details={"agent_id": agent_id},
    )
    return updated


def record_metric(
    *,
    metric_type: str,
    agent_id: str = "",
    run_id: str = "",
    workflow_id: str = "",
    step_id: str = "",
    ok: bool | None = None,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    return monitoring_store.record_metric(
        DB_PATH,
        metric_type=metric_type,
        agent_id=agent_id,
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        ok=ok,
        duration_ms=duration_ms,
        details=details,
        created_at=created_at,
    )


def record_resource_usage(
    *,
    agent_id: str,
    resource: str,
    amount: float,
    unit: str = "",
    run_id: str = "",
    workflow_id: str = "",
    step_id: str = "",
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    return monitoring_store.record_resource_usage(
        DB_PATH,
        agent_id=agent_id,
        resource=resource,
        amount=amount,
        unit=unit,
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        details=details,
        created_at=created_at,
    )


def record_agent_run_metric(
    *,
    agent_id: str,
    run_id: str,
    route: str,
    model_name: str,
    ok: bool,
    duration_ms: int,
    streaming: bool = False,
    num_ctx: int = 0,
    tools: list[str] | None = None,
) -> None:
    monitoring_store.record_agent_run_metric(
        DB_PATH,
        agent_id=agent_id,
        run_id=run_id,
        route=route,
        model_name=model_name,
        ok=ok,
        duration_ms=duration_ms,
        streaming=streaming,
        num_ctx=num_ctx,
        tools=tools,
    )


def record_workflow_run_metric(
    *,
    workflow_id: str,
    run_id: str,
    status: str,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> None:
    monitoring_store.record_workflow_run_metric(
        DB_PATH,
        workflow_id=workflow_id,
        run_id=run_id,
        status=status,
        duration_ms=duration_ms,
        details=details,
        workflow_engine_agent_id=WORKFLOW_ENGINE_AGENT_ID,
    )


def record_workflow_step_metric(
    *,
    agent_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    step_type: str,
    ok: bool,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> None:
    monitoring_store.record_workflow_step_metric(
        DB_PATH,
        agent_id=agent_id,
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        step_type=step_type,
        ok=ok,
        duration_ms=duration_ms,
        details=details,
    )


def record_sandbox_block(
    *,
    agent_id: str,
    reason: str,
    run_id: str = "",
    workflow_id: str = "",
    step_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    payload = {"reason": reason, **(details or {})}
    record_metric(
        metric_type="sandbox.blocked",
        agent_id=agent_id,
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        ok=False,
        details=payload,
    )
    try:
        from app.application.event_bus.runtime import emit_event

        emit_event(
            event_type="sandbox.policy.blocked",
            source_agent_id=agent_id,
            payload={
                "agent_id": agent_id,
                "run_id": run_id,
                "workflow_id": workflow_id,
                "step_id": step_id,
                **payload,
            },
        )
    except Exception:
        pass


def count_agent_runs_last_hour(agent_id: str) -> int:
    return monitoring_store.count_agent_runs_last_hour(DB_PATH, agent_id)


def get_recent_blocked_runs(
    hours: int = 24,
    limit: int = 10,
) -> list[dict[str, Any]]:
    return monitoring_store.get_recent_blocked_runs(
        DB_PATH,
        hours=hours,
        limit=limit,
    )


def get_agent_os_health() -> dict[str, Any]:
    seed_default_limits()
    return monitoring_reporting.get_agent_os_health(
        db_path=DB_PATH,
        workflow_db_path=get_workflow_db_path(),
    )


def get_agent_os_dashboard(window_hours: int = 24) -> dict[str, Any]:
    seed_default_limits()
    return monitoring_reporting.get_agent_os_dashboard(
        db_path=DB_PATH,
        window_hours=window_hours,
        ensure_agent_limit=ensure_agent_limit,
        workflow_engine_agent_id=WORKFLOW_ENGINE_AGENT_ID,
    )


# ── Approvals ─────────────────────────────────────────────────────────────────

def create_approval(
    *,
    id: str,
    tool_name: str,
    agent_id: str = "",
    source: str = "",
    run_id: str = "",
    project_scope_id: str = "",
    args: dict[str, Any] | None = None,
    ttl_seconds: int = 300,
) -> dict[str, Any]:
    return monitoring_store.create_approval(
        DB_PATH,
        id=id,
        tool_name=tool_name,
        agent_id=agent_id,
        source=source,
        run_id=run_id,
        project_scope_id=project_scope_id,
        args=args,
        ttl_seconds=ttl_seconds,
    )


def get_approval(approval_id: str) -> dict[str, Any] | None:
    return monitoring_store.get_approval(DB_PATH, approval_id)


def list_approvals(
    *,
    status: str | None = None,
    agent_id: str | None = None,
    tool_name: str | None = None,
    run_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return monitoring_store.list_approvals(
        DB_PATH,
        status=status,
        agent_id=agent_id,
        tool_name=tool_name,
        run_id=run_id,
        limit=limit,
    )


def update_approval_status(approval_id: str, *, status: str) -> dict[str, Any] | None:
    return monitoring_store.update_approval_status(DB_PATH, approval_id, status=status)


def find_approved_approval(
    *,
    tool_name: str,
    agent_id: str,
    source: str,
    run_id: str,
    project_scope_id: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return monitoring_store.find_approved_approval(
        DB_PATH,
        tool_name=tool_name,
        agent_id=agent_id,
        source=source,
        run_id=run_id,
        project_scope_id=project_scope_id,
        args=args,
    )


def expire_old_approvals() -> int:
    return monitoring_store.expire_old_approvals(DB_PATH)


# ── MemoryCandidate ───────────────────────────────────────────────────────────

def create_candidate(
    *,
    id: str,
    namespace: str = "project",
    content: str,
    source: str = "",
    confidence: float = 1.0,
    project_scope_id: str = "",
    expires_at: str | None = None,
) -> dict[str, Any]:
    return monitoring_store.create_candidate(
        DB_PATH, id=id, namespace=namespace, content=content,
        source=source, confidence=confidence,
        project_scope_id=project_scope_id, expires_at=expires_at,
    )


def get_candidate(candidate_id: str) -> dict[str, Any] | None:
    return monitoring_store.get_candidate(DB_PATH, candidate_id)


def list_candidates(
    *,
    status: str | None = None,
    namespace: str | None = None,
    project_scope_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return monitoring_store.list_candidates(
        DB_PATH, status=status, namespace=namespace,
        project_scope_id=project_scope_id, limit=limit,
    )


def update_candidate_status(
    candidate_id: str,
    *,
    status: str,
    content: str | None = None,
) -> dict[str, Any] | None:
    return monitoring_store.update_candidate_status(
        DB_PATH, candidate_id, status=status, content=content,
    )


def delete_candidate(candidate_id: str) -> dict[str, Any]:
    return monitoring_store.delete_candidate(DB_PATH, candidate_id)


def list_accepted_candidates(
    *,
    namespace: str = "project",
    project_scope_id: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    return monitoring_store.list_accepted_candidates(
        DB_PATH, namespace=namespace,
        project_scope_id=project_scope_id, limit=limit,
    )


# ── Model Profiles ────────────────────────────────────────────────────────────

def list_model_profiles(*, role: str | None = None, enabled_only: bool = False) -> list[dict[str, Any]]:
    return monitoring_store.list_model_profiles(DB_PATH, role=role, enabled_only=enabled_only)


def get_model_profile(profile_id: str) -> dict[str, Any] | None:
    return monitoring_store.get_model_profile(DB_PATH, profile_id)


def enable_model_profile(profile_id: str) -> dict[str, Any] | None:
    return monitoring_store.set_model_profile_enabled(DB_PATH, profile_id, enabled=True)


def disable_model_profile(profile_id: str) -> dict[str, Any] | None:
    return monitoring_store.set_model_profile_enabled(DB_PATH, profile_id, enabled=False)


def get_profile_for_role(role: str) -> dict[str, Any] | None:
    return monitoring_store.get_profile_for_role(DB_PATH, role)
