"""Legacy workflow-engine monolith kept for compatibility during extraction."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.application.workflows.runtime import (
    cancel_workflow_run as _app_cancel_workflow_run,
    resume_workflow_run as _app_resume_workflow_run,
    start_workflow_run as _app_start_workflow_run,
)
from app.application.workflows.store import (
    create_workflow_template as _app_create_workflow_template,
    delete_workflow_template as _app_delete_workflow_template,
    get_workflow_run as _app_get_workflow_run,
    get_workflow_template as _app_get_workflow_template,
    init_db as _app_init_db,
    list_workflow_runs as _app_list_workflow_runs,
    list_workflow_templates as _app_list_workflow_templates,
    now_utc as _app_now_utc,
    update_workflow_template as _app_update_workflow_template,
    upsert_workflow_template as _app_upsert_workflow_template,
)
from app.core.data_files import sqlite_data_file


DB_PATH: Path = sqlite_data_file("workflow_engine.db")


def _now() -> str:
    return _app_now_utc()


def _init_db() -> None:
    _app_init_db(db_path=DB_PATH)


_init_db()


def _upsert_workflow_template(template: dict[str, Any]) -> dict[str, Any]:
    return _app_upsert_workflow_template(
        db_path=DB_PATH,
        template=template,
        now_func=_now,
    )


def create_workflow_template(template: dict[str, Any]) -> dict[str, Any]:
    return _app_create_workflow_template(db_path=DB_PATH, template=template)


def get_workflow_template(workflow_id: str) -> dict[str, Any] | None:
    return _app_get_workflow_template(db_path=DB_PATH, workflow_id=workflow_id)


def list_workflow_templates(
    *,
    include_disabled: bool = False,
    source: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    return _app_list_workflow_templates(
        db_path=DB_PATH,
        include_disabled=include_disabled,
        source=source,
    )


def update_workflow_template(workflow_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    return _app_update_workflow_template(
        db_path=DB_PATH,
        workflow_id=workflow_id,
        updates=updates,
    )


def delete_workflow_template(workflow_id: str) -> dict[str, Any]:
    return _app_delete_workflow_template(db_path=DB_PATH, workflow_id=workflow_id)


def get_workflow_run(run_id: str) -> dict[str, Any] | None:
    return _app_get_workflow_run(db_path=DB_PATH, run_id=run_id)


def list_workflow_runs(
    *,
    workflow_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    return _app_list_workflow_runs(
        db_path=DB_PATH,
        workflow_id=workflow_id,
        status=status,
        limit=limit,
        offset=offset,
    )


# ═══════════════════════════════════════════════════════════════
# STEP EXECUTION -- extracted to domain/workflows/step_executor.py
# ═══════════════════════════════════════════════════════════════

def start_workflow_run(
    *,
    workflow_id: str,
    workflow_input: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    trigger_source: str = "api",
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    return _app_start_workflow_run(
        db_path=DB_PATH,
        workflow_id=workflow_id,
        workflow_input=workflow_input,
        context=context,
        trigger_source=trigger_source,
        progress_callback=progress_callback,
    )


def resume_workflow_run(
    run_id: str,
    *,
    context_patch: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    return _app_resume_workflow_run(
        run_id,
        db_path=DB_PATH,
        context_patch=context_patch,
        progress_callback=progress_callback,
    )


def cancel_workflow_run(run_id: str) -> dict[str, Any]:
    return _app_cancel_workflow_run(
        run_id,
        db_path=DB_PATH,
    )




# ═══════════════════════════════════════════════════════════════
# MULTI-AGENT WORKFLOWS -- extracted to application/workflows/multi_agent.py
# ═══════════════════════════════════════════════════════════════

from app.application.workflows.multi_agent import (  # noqa: E402
    MULTI_AGENT_DEFAULT_WORKFLOW_ID,
    MULTI_AGENT_REFLECTION_WORKFLOW_ID,
    MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID,
    MULTI_AGENT_FULL_WORKFLOW_ID,
    seed_builtin_workflows,
    run_multi_agent_workflow,
    run_legacy_multi_agent_workflow,
)
