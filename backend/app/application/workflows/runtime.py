from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from app.application.workflows.db_path import get_workflow_db_path
from app.application.workflows.execution import (
    build_workflow_execution_state,
    record_workflow_run_state,
    record_workflow_step_state,
    record_workflow_total_steps,
    workflow_step_index,
)
from app.application.workflows.events import emit_workflow_event
from app.application.workflows.lifecycle import (
    advance_to_next_step,
    cancel_run,
    complete_after_step,
    fail_missing_step,
    fail_step_and_finish,
    merge_resumed_context,
    pause_after_step,
)
from app.application.workflows.step_results import (
    build_step_completion_event,
    build_step_result_from_exception,
    capture_step_outcome,
    should_pause_after_step,
)
from app.application.workflows.store import (
    create_workflow_run_record,
    get_workflow_run,
    get_workflow_template,
    init_db,
    now_utc,
    update_workflow_run,
)
from app.domain.workflows.step_executor import (
    _execute_step,
    _resolve_next_step,
    _step_label,
)


def _resolve_db_path(db_path: str | Path | None = None) -> str | Path:
    return db_path or get_workflow_db_path()


def _update_workflow_run_for_db(
    db_path: str | Path,
    run_id: str,
    **fields: Any,
) -> dict[str, Any]:
    return update_workflow_run(
        db_path=db_path,
        run_id=run_id,
        now_func=now_utc,
        **fields,
    )


def execute_workflow_run(
    *,
    run_id: str,
    db_path: str | Path | None = None,
    resume_event: bool = False,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    resolved_db_path = _resolve_db_path(db_path)
    init_db(db_path=resolved_db_path)

    def _get_workflow_run(run_key: str) -> dict[str, Any] | None:
        return get_workflow_run(db_path=resolved_db_path, run_id=run_key)

    def _update_run(run_key: str, **fields: Any) -> dict[str, Any]:
        return _update_workflow_run_for_db(resolved_db_path, run_key, **fields)

    run = _get_workflow_run(run_id)
    if not run:
        raise ValueError(f"Workflow run '{run_id}' not found")

    template = get_workflow_template(db_path=resolved_db_path, workflow_id=run["workflow_id"])
    if not template:
        raise ValueError(f"Workflow '{run['workflow_id']}' not found")

    execution_state = build_workflow_execution_state(
        run=run,
        template=template,
        get_workflow_run=_get_workflow_run,
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
        emit_workflow_event("workflow.run.resumed", run["workflow_id"], run_id, payload={"current_step_id": current_step_id})
        record_workflow_run_state(run_state, status="resumed", details={"current_step_id": current_step_id})
    else:
        emit_workflow_event("workflow.run.started", run["workflow_id"], run_id, payload={"current_step_id": current_step_id})
        record_workflow_run_state(
            run_state,
            status="started",
            details={"current_step_id": current_step_id, "total_steps": total_steps},
        )
        record_workflow_total_steps(run, run_id=run_id, total_steps=total_steps)

    while current_step_id:
        step = steps_by_id.get(current_step_id)
        if not step:
            return fail_missing_step(
                run_id=run_id,
                workflow_id=run["workflow_id"],
                current_step_id=current_step_id,
                update_workflow_run=_update_run,
                record_workflow_run_state=record_workflow_run_state,
                emit_workflow_event=emit_workflow_event,
                now_func=now_utc,
            )

        step_index = workflow_step_index(current_step_id, ordered_ids, step_results)
        step_label = _step_label(step)
        if progress_callback:
            progress_callback(step_index, total_steps, step_label)

        emit_workflow_event(
            "workflow.step.started",
            run["workflow_id"],
            run_id,
            payload={"step_id": current_step_id, "index": step_index},
        )
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
            step_result = build_step_result_from_exception(exc)
        step_duration_ms = int((time.monotonic() - step_started) * 1000)

        step_outcome = capture_step_outcome(
            step,
            current_step_id=current_step_id,
            step_result=step_result,
            step_results=step_results,
            resolve_next_step=lambda current_step: _resolve_next_step(
                current_step,
                success=bool(step_result.get("ok")),
            ),
        )
        record_workflow_step_state(
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

        completion_event_type, completion_payload = build_step_completion_event(
            current_step_id=current_step_id,
            outcome=step_outcome,
            step_result=step_result,
        )
        emit_workflow_event(completion_event_type, run["workflow_id"], run_id, payload=completion_payload)

        if should_pause_after_step(step, step_result) and step_outcome.success and step_outcome.next_step_id:
            return pause_after_step(
                run_id=run_id,
                workflow_id=run["workflow_id"],
                current_step_id=current_step_id,
                next_step_id=step_outcome.next_step_id,
                step_results=step_results,
                update_workflow_run=_update_run,
                record_workflow_run_state=record_workflow_run_state,
                emit_workflow_event=emit_workflow_event,
            )

        if not step_outcome.success and not step_outcome.next_step_id:
            return fail_step_and_finish(
                run_id=run_id,
                workflow_id=run["workflow_id"],
                current_step_id=current_step_id,
                step_results=step_results,
                error_message=str(step_result.get("error", "step failed")),
                update_workflow_run=_update_run,
                record_workflow_run_state=record_workflow_run_state,
                emit_workflow_event=emit_workflow_event,
                now_func=now_utc,
            )

        if not step_outcome.next_step_id:
            return complete_after_step(
                run_id=run_id,
                workflow_id=run["workflow_id"],
                completed_from_step_id=current_step_id,
                step_results=step_results,
                update_workflow_run=_update_run,
                record_workflow_run_state=record_workflow_run_state,
                emit_workflow_event=emit_workflow_event,
                now_func=now_utc,
            )

        current_step_id = step_outcome.next_step_id
        advance_to_next_step(
            run_id=run_id,
            next_step_id=current_step_id,
            step_results=step_results,
            update_workflow_run=_update_run,
        )

    return complete_after_step(
        run_id=run_id,
        workflow_id=run["workflow_id"],
        completed_from_step_id="",
        step_results=step_results,
        update_workflow_run=_update_run,
        record_workflow_run_state=record_workflow_run_state,
        emit_workflow_event=emit_workflow_event,
        now_func=now_utc,
    )


def start_workflow_run(
    *,
    workflow_id: str,
    workflow_input: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    trigger_source: str = "api",
    progress_callback: Callable[[int, int, str], None] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    resolved_db_path = _resolve_db_path(db_path)
    init_db(db_path=resolved_db_path)
    run = create_workflow_run_record(
        db_path=resolved_db_path,
        workflow_id=workflow_id,
        workflow_input=workflow_input,
        context=context,
        trigger_source=trigger_source,
        now_func=now_utc,
    )
    return execute_workflow_run(
        run_id=run["run_id"],
        db_path=resolved_db_path,
        progress_callback=progress_callback,
    )


def resume_workflow_run(
    run_id: str,
    *,
    context_patch: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    resolved_db_path = _resolve_db_path(db_path)
    init_db(db_path=resolved_db_path)
    run = get_workflow_run(db_path=resolved_db_path, run_id=run_id)
    if not run:
        raise ValueError(f"Workflow run '{run_id}' not found")
    if run["status"] != "paused":
        raise ValueError("Only paused workflow runs can be resumed")

    merged_context = merge_resumed_context(run, context_patch)
    _update_workflow_run_for_db(
        resolved_db_path,
        run_id,
        status="running",
        context=merged_context,
        error={},
        requested_pause=False,
    )
    return execute_workflow_run(
        run_id=run_id,
        db_path=resolved_db_path,
        resume_event=True,
        progress_callback=progress_callback,
    )


def cancel_workflow_run(
    run_id: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    resolved_db_path = _resolve_db_path(db_path)
    init_db(db_path=resolved_db_path)
    run = get_workflow_run(db_path=resolved_db_path, run_id=run_id)
    if not run:
        raise ValueError(f"Workflow run '{run_id}' not found")
    if run["status"] in {"completed", "failed", "cancelled"}:
        raise ValueError("Terminal workflow runs cannot be cancelled")

    return cancel_run(
        run_id=run_id,
        run=run,
        update_workflow_run=lambda current_run_id, **fields: _update_workflow_run_for_db(
            resolved_db_path,
            current_run_id,
            **fields,
        ),
        record_workflow_run_state=record_workflow_run_state,
        emit_workflow_event=emit_workflow_event,
        now_func=now_utc,
    )
