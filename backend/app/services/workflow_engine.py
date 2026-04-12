"""Legacy workflow-engine monolith kept for compatibility during extraction."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from app.application.workflows.execution import (
    build_workflow_execution_state as _app_build_workflow_execution_state,
    record_workflow_step_state as _app_record_workflow_step_state,
    record_workflow_total_steps as _app_record_workflow_total_steps,
    record_workflow_run_state as _app_record_workflow_run_state,
    workflow_step_index as _app_workflow_step_index,
)
from app.application.workflows.lifecycle import (
    advance_to_next_step as _app_advance_to_next_step,
    cancel_run as _app_cancel_run,
    complete_after_step as _app_complete_after_step,
    fail_missing_step as _app_fail_missing_step,
    fail_step_and_finish as _app_fail_step_and_finish,
    merge_resumed_context as _app_merge_resumed_context,
    pause_after_step as _app_pause_after_step,
)
from app.application.workflows.step_results import (
    build_step_completion_event as _app_build_step_completion_event,
    build_step_result_from_exception as _app_build_step_result_from_exception,
    capture_step_outcome as _app_capture_step_outcome,
    should_pause_after_step as _app_should_pause_after_step,
)
from app.application.workflows.store import (
    create_workflow_run_record as _app_create_workflow_run_record,
    create_workflow_template as _app_create_workflow_template,
    delete_workflow_template as _app_delete_workflow_template,
    get_workflow_run as _app_get_workflow_run,
    get_workflow_template as _app_get_workflow_template,
    init_db as _app_init_db,
    list_workflow_runs as _app_list_workflow_runs,
    list_workflow_templates as _app_list_workflow_templates,
    now_utc as _app_now_utc,
    update_workflow_run as _app_update_workflow_run,
    update_workflow_template as _app_update_workflow_template,
    upsert_workflow_template as _app_upsert_workflow_template,
)
from app.core.data_files import sqlite_data_file


DB_PATH: Path = sqlite_data_file("workflow_engine.db")

TERMINAL_STATUSES = {"completed", "failed", "paused", "cancelled"}


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


def _update_workflow_run(run_id: str, **fields: Any) -> dict[str, Any]:
    return _app_update_workflow_run(
        db_path=DB_PATH,
        run_id=run_id,
        now_func=_now,
        **fields,
    )


def _create_workflow_run_record(
    *,
    workflow_id: str,
    workflow_input: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    trigger_source: str = "api",
) -> dict[str, Any]:
    return _app_create_workflow_run_record(
        db_path=DB_PATH,
        workflow_id=workflow_id,
        workflow_input=workflow_input,
        context=context,
        trigger_source=trigger_source,
        now_func=_now,
    )


def _emit_workflow_event(event_type: str, workflow_id: str, run_id: str, payload: dict[str, Any] | None = None) -> None:
    try:
        from app.services.event_bus import emit_event

        emit_event(
            event_type=event_type,
            source_agent_id=workflow_id,
            payload={"workflow_id": workflow_id, "run_id": run_id, **(payload or {})},
        )
    except Exception:
        return


# ═══════════════════════════════════════════════════════════════
# STEP EXECUTION -- extracted to domain/workflows/step_executor.py
# ═══════════════════════════════════════════════════════════════

from app.domain.workflows.step_executor import (  # noqa: E402
    _resolve_path,
    _resolve_input_expression,
    _map_step_inputs,
    _stringify_template_value,
    _SafeFormatDict,
    _render_prompt_template,
    _determine_profile_name,
    _execute_agent_step,
    _execute_tool_step,
    _execute_step,
    _resolve_next_step,
    _step_label,
    STEP_SUCCESS,
    STEP_FAILURE,
)


def _execute_workflow_run(
    run_id: str,
    *,
    resume_event: bool = False,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    run = get_workflow_run(run_id)
    if not run:
        raise ValueError(f"Workflow run '{run_id}' not found")

    template = get_workflow_template(run["workflow_id"])
    if not template:
        raise ValueError(f"Workflow '{run['workflow_id']}' not found")

    execution_state = _app_build_workflow_execution_state(
        run=run,
        template=template,
        get_workflow_run=get_workflow_run,
    )
    steps_by_id = execution_state.steps_by_id
    ordered_ids = execution_state.ordered_ids
    total_steps = execution_state.total_steps
    workflow_input = execution_state.workflow_input
    context = execution_state.context
    step_results = execution_state.step_results
    current_step_id = execution_state.current_step_id
    run_state = execution_state.run_state

    if resume_event:
        _emit_workflow_event("workflow.run.resumed", run["workflow_id"], run_id, payload={"current_step_id": current_step_id})
        _app_record_workflow_run_state(run_state, status="resumed", details={"current_step_id": current_step_id})
    else:
        _emit_workflow_event("workflow.run.started", run["workflow_id"], run_id, payload={"current_step_id": current_step_id})
        _app_record_workflow_run_state(run_state, status="started", details={"current_step_id": current_step_id, "total_steps": total_steps})
        _app_record_workflow_total_steps(run, run_id=run_id, total_steps=total_steps)

    while current_step_id:
        step = steps_by_id.get(current_step_id)
        if not step:
            return _app_fail_missing_step(
                run_id=run_id,
                workflow_id=run["workflow_id"],
                current_step_id=current_step_id,
                update_workflow_run=_update_workflow_run,
                record_workflow_run_state=_app_record_workflow_run_state,
                emit_workflow_event=_emit_workflow_event,
                now_func=_now,
            )

        step_index = _app_workflow_step_index(current_step_id, ordered_ids, step_results)
        step_label = _step_label(step)
        if progress_callback:
            progress_callback(step_index, total_steps, step_label)

        _emit_workflow_event("workflow.step.started", run["workflow_id"], run_id, payload={"step_id": current_step_id, "index": step_index})
        step_started = time.monotonic()
        try:
            step_result = _execute_step(
                step,
                workflow_id=run["workflow_id"],
                workflow_input=workflow_input,
                context=context,
                step_results=step_results,
                run_id=run_id,
            )
        except Exception as exc:
            step_result = _app_build_step_result_from_exception(exc)
        step_duration_ms = int((time.monotonic() - step_started) * 1000)

        step_outcome = _app_capture_step_outcome(
            step,
            current_step_id=current_step_id,
            step_result=step_result,
            step_results=step_results,
            resolve_next_step=lambda current_step: _resolve_next_step(
                current_step,
                success=bool(step_result.get("ok")),
            ),
        )
        _app_record_workflow_step_state(
            step,
            workflow_id=run["workflow_id"],
            run_id=run_id,
            step_id=current_step_id,
            step_result=step_result,
            step_index=step_index,
            step_duration_ms=step_duration_ms,
            next_step_id=step_outcome.next_step_id,
            step_label=step_label,
        )

        completion_event_type, completion_payload = _app_build_step_completion_event(
            current_step_id=current_step_id,
            outcome=step_outcome,
            step_result=step_result,
        )
        _emit_workflow_event(completion_event_type, run["workflow_id"], run_id, payload=completion_payload)

        if _app_should_pause_after_step(step, step_result) and step_outcome.success and step_outcome.next_step_id:
            return _app_pause_after_step(
                run_id=run_id,
                workflow_id=run["workflow_id"],
                current_step_id=current_step_id,
                next_step_id=step_outcome.next_step_id,
                step_results=step_results,
                update_workflow_run=_update_workflow_run,
                record_workflow_run_state=_app_record_workflow_run_state,
                emit_workflow_event=_emit_workflow_event,
            )

        if not step_outcome.success and not step_outcome.next_step_id:
            return _app_fail_step_and_finish(
                run_id=run_id,
                workflow_id=run["workflow_id"],
                current_step_id=current_step_id,
                step_results=step_results,
                error_message=str(step_result.get("error", "step failed")),
                update_workflow_run=_update_workflow_run,
                record_workflow_run_state=_app_record_workflow_run_state,
                emit_workflow_event=_emit_workflow_event,
                now_func=_now,
            )

        if not step_outcome.next_step_id:
            return _app_complete_after_step(
                run_id=run_id,
                workflow_id=run["workflow_id"],
                completed_from_step_id=current_step_id,
                step_results=step_results,
                update_workflow_run=_update_workflow_run,
                record_workflow_run_state=_app_record_workflow_run_state,
                emit_workflow_event=_emit_workflow_event,
                now_func=_now,
            )

        current_step_id = step_outcome.next_step_id
        _app_advance_to_next_step(
            run_id=run_id,
            next_step_id=current_step_id,
            step_results=step_results,
            update_workflow_run=_update_workflow_run,
        )

    return _app_complete_after_step(
        run_id=run_id,
        workflow_id=run["workflow_id"],
        completed_from_step_id="",
        step_results=step_results,
        update_workflow_run=_update_workflow_run,
        record_workflow_run_state=_app_record_workflow_run_state,
        emit_workflow_event=_emit_workflow_event,
        now_func=_now,
    )


def start_workflow_run(
    *,
    workflow_id: str,
    workflow_input: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    trigger_source: str = "api",
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    run = _create_workflow_run_record(
        workflow_id=workflow_id,
        workflow_input=workflow_input,
        context=context,
        trigger_source=trigger_source,
    )
    return _execute_workflow_run(run["run_id"], progress_callback=progress_callback)


def resume_workflow_run(
    run_id: str,
    *,
    context_patch: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    run = get_workflow_run(run_id)
    if not run:
        raise ValueError(f"Workflow run '{run_id}' not found")
    if run["status"] != "paused":
        raise ValueError("Only paused workflow runs can be resumed")

    merged_context = _app_merge_resumed_context(run, context_patch)
    _update_workflow_run(run_id, status="running", context=merged_context, error={}, requested_pause=False)
    return _execute_workflow_run(run_id, resume_event=True, progress_callback=progress_callback)


def cancel_workflow_run(run_id: str) -> dict[str, Any]:
    run = get_workflow_run(run_id)
    if not run:
        raise ValueError(f"Workflow run '{run_id}' not found")
    if run["status"] in {"completed", "failed", "cancelled"}:
        raise ValueError("Terminal workflow runs cannot be cancelled")

    return _app_cancel_run(
        run_id=run_id,
        run=run,
        update_workflow_run=_update_workflow_run,
        record_workflow_run_state=_app_record_workflow_run_state,
        emit_workflow_event=_emit_workflow_event,
        now_func=_now,
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
