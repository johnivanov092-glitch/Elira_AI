from __future__ import annotations

from typing import Any, Callable

WorkflowRunUpdater = Callable[..., dict[str, Any]]
WorkflowRunStateRecorder = Callable[..., None]
WorkflowEventEmitter = Callable[[str, str, str, dict[str, Any] | None], None]
NowFunc = Callable[[], str]


def merge_resumed_context(
    run: dict[str, Any],
    context_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged_context = dict(run.get("context", {}))
    merged_context.update(context_patch or {})
    return merged_context


def fail_missing_step(
    *,
    run_id: str,
    workflow_id: str,
    current_step_id: str,
    update_workflow_run: WorkflowRunUpdater,
    record_workflow_run_state: WorkflowRunStateRecorder,
    emit_workflow_event: WorkflowEventEmitter,
    now_func: NowFunc,
) -> dict[str, Any]:
    error = {"message": f"Workflow step '{current_step_id}' not found"}
    failed_run = update_workflow_run(
        run_id,
        status="failed",
        current_step_id=current_step_id,
        error=error,
        finished_at=now_func(),
    )
    record_workflow_run_state(
        failed_run,
        status="failed",
        details={"current_step_id": current_step_id, "reason": "step_not_found"},
    )
    emit_workflow_event(
        "workflow.step.failed",
        workflow_id,
        run_id,
        payload={"step_id": current_step_id, "error": error["message"]},
    )
    emit_workflow_event(
        "workflow.run.completed",
        workflow_id,
        run_id,
        payload={"ok": False, "status": "failed"},
    )
    return failed_run


def pause_after_step(
    *,
    run_id: str,
    workflow_id: str,
    current_step_id: str,
    next_step_id: str,
    step_results: dict[str, Any],
    update_workflow_run: WorkflowRunUpdater,
    record_workflow_run_state: WorkflowRunStateRecorder,
    emit_workflow_event: WorkflowEventEmitter,
) -> dict[str, Any]:
    paused_run = update_workflow_run(
        run_id,
        status="paused",
        current_step_id=next_step_id,
        step_results=step_results,
        pending_steps=[next_step_id],
        requested_pause=False,
    )
    record_workflow_run_state(
        paused_run,
        status="paused",
        details={"current_step_id": next_step_id, "after_step_id": current_step_id},
    )
    emit_workflow_event(
        "workflow.run.paused",
        workflow_id,
        run_id,
        payload={"current_step_id": next_step_id},
    )
    return paused_run


def fail_step_and_finish(
    *,
    run_id: str,
    workflow_id: str,
    current_step_id: str,
    step_results: dict[str, Any],
    error_message: str,
    update_workflow_run: WorkflowRunUpdater,
    record_workflow_run_state: WorkflowRunStateRecorder,
    emit_workflow_event: WorkflowEventEmitter,
    now_func: NowFunc,
) -> dict[str, Any]:
    failed_run = update_workflow_run(
        run_id,
        status="failed",
        current_step_id=current_step_id,
        step_results=step_results,
        pending_steps=[],
        error={"step_id": current_step_id, "message": error_message},
        finished_at=now_func(),
    )
    record_workflow_run_state(
        failed_run,
        status="failed",
        details={"current_step_id": current_step_id},
    )
    emit_workflow_event(
        "workflow.run.completed",
        workflow_id,
        run_id,
        payload={"ok": False, "status": "failed", "step_id": current_step_id},
    )
    return failed_run


def complete_after_step(
    *,
    run_id: str,
    workflow_id: str,
    completed_from_step_id: str,
    step_results: dict[str, Any],
    update_workflow_run: WorkflowRunUpdater,
    record_workflow_run_state: WorkflowRunStateRecorder,
    emit_workflow_event: WorkflowEventEmitter,
    now_func: NowFunc,
) -> dict[str, Any]:
    completed_run = update_workflow_run(
        run_id,
        status="completed",
        current_step_id="",
        step_results=step_results,
        pending_steps=[],
        error={},
        finished_at=now_func(),
    )
    record_workflow_run_state(
        completed_run,
        status="completed",
        details={"completed_from_step_id": completed_from_step_id},
    )
    emit_workflow_event(
        "workflow.run.completed",
        workflow_id,
        run_id,
        payload={"ok": True, "status": "completed"},
    )
    return completed_run


def advance_to_next_step(
    *,
    run_id: str,
    next_step_id: str,
    step_results: dict[str, Any],
    update_workflow_run: WorkflowRunUpdater,
) -> dict[str, Any]:
    return update_workflow_run(
        run_id,
        status="running",
        current_step_id=next_step_id,
        step_results=step_results,
        pending_steps=[next_step_id],
        error={},
    )


def cancel_run(
    *,
    run_id: str,
    run: dict[str, Any],
    update_workflow_run: WorkflowRunUpdater,
    record_workflow_run_state: WorkflowRunStateRecorder,
    emit_workflow_event: WorkflowEventEmitter,
    now_func: NowFunc,
) -> dict[str, Any]:
    cancelled = update_workflow_run(
        run_id,
        status="cancelled",
        pending_steps=[],
        finished_at=now_func(),
    )
    record_workflow_run_state(
        cancelled,
        status="cancelled",
        details={"current_step_id": cancelled.get("current_step_id", "")},
    )
    emit_workflow_event(
        "workflow.run.cancelled",
        str(run.get("workflow_id", "")),
        run_id,
        payload={"status": "cancelled"},
    )
    return cancelled
