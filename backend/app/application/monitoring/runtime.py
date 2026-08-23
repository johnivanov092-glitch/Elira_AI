from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.application.monitoring import store as monitoring_store
from app.core.data_files import sqlite_data_file


DB_PATH: Path = sqlite_data_file("agent_monitor.db")

WORKFLOW_ENGINE_AGENT_ID = monitoring_store.DEFAULT_WORKFLOW_ENGINE_AGENT_ID


def _init_db() -> None:
    monitoring_store.init_db(DB_PATH)
    monitoring_store.migrate_memory_candidates_table(DB_PATH)
    monitoring_store.migrate_model_profiles_table(DB_PATH)


_init_db()


def prune_metrics(max_age_days: int = 45) -> dict[str, Any]:
    """Retention for agent_monitor.db — delete telemetry older than max_age_days
    and VACUUM. Called on a daily cadence from the task-recovery scheduler so the
    DB stops growing monotonically."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    return monitoring_store.prune_old_metrics(DB_PATH, cutoff_iso=cutoff)


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
