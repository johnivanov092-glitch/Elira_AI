"""Legacy workflow-engine monolith kept for compatibility during extraction."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from app.application.workflows.execution import (
    WorkflowExecutionState,
    build_workflow_execution_state as _app_build_workflow_execution_state,
    parse_workflow_datetime as _app_parse_workflow_datetime,
    record_workflow_run_state as _app_record_workflow_run_state,
    record_workflow_step_state as _app_record_workflow_step_state,
    record_workflow_total_steps as _app_record_workflow_total_steps,
    workflow_duration_ms as _app_workflow_duration_ms,
    workflow_step_index as _app_workflow_step_index,
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
    normalize_graph as _app_normalize_graph,
    now_utc as _app_now_utc,
    update_workflow_run as _app_update_workflow_run,
    update_workflow_template as _app_update_workflow_template,
    upsert_workflow_template as _app_upsert_workflow_template,
)
from app.core.data_files import sqlite_data_file
from app.services.agent_sandbox import SandboxPolicyError, preflight_or_raise


DB_PATH: Path = sqlite_data_file("workflow_engine.db")

TERMINAL_STATUSES = {"completed", "failed", "paused", "cancelled"}
STEP_SUCCESS = "on_success"
STEP_FAILURE = "on_failure"

MULTI_AGENT_DEFAULT_WORKFLOW_ID = "builtin.workflow.multi_agent.default"
MULTI_AGENT_REFLECTION_WORKFLOW_ID = "builtin.workflow.multi_agent.reflection"
MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID = "builtin.workflow.multi_agent.orchestrated"
MULTI_AGENT_FULL_WORKFLOW_ID = "builtin.workflow.multi_agent.full"

def _now() -> str:
    return _app_now_utc()


def _init_db() -> None:
    _app_init_db(db_path=DB_PATH)


_init_db()


def _normalize_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return _app_normalize_graph(graph)


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


def _parse_dt(raw: str | None) -> datetime | None:
    return _app_parse_workflow_datetime(raw)


def _workflow_duration_ms(run: dict[str, Any]) -> int:
    return _app_workflow_duration_ms(run)


def _record_workflow_run_state(run: dict[str, Any], *, status: str, details: dict[str, Any] | None = None) -> None:
    _app_record_workflow_run_state(run, status=status, details=details)



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


def _build_workflow_execution_state(run: dict[str, Any], template: dict[str, Any]) -> WorkflowExecutionState:
    return _app_build_workflow_execution_state(
        run=run,
        template=template,
        get_workflow_run=get_workflow_run,
    )


def _run_state_for_execution(run: dict[str, Any], template: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    execution_state = _build_workflow_execution_state(run, template)
    return execution_state.graph, execution_state.steps_by_id, execution_state.ordered_ids


def _record_workflow_total_steps(run: dict[str, Any], *, run_id: str, total_steps: int) -> None:
    _app_record_workflow_total_steps(run, run_id=run_id, total_steps=total_steps)


def _workflow_step_index(current_step_id: str, ordered_ids: list[str], step_results: dict[str, Any]) -> int:
    return _app_workflow_step_index(current_step_id, ordered_ids, step_results)


def _record_workflow_step_state(
    step: dict[str, Any],
    *,
    workflow_id: str,
    run_id: str,
    step_id: str,
    step_result: dict[str, Any],
    step_index: int,
    step_duration_ms: int,
    next_step_id: str | None,
    step_label: str,
) -> None:
    _app_record_workflow_step_state(
        step,
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        step_result=step_result,
        step_index=step_index,
        step_duration_ms=step_duration_ms,
        next_step_id=next_step_id,
        step_label=step_label,
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

    execution_state = _build_workflow_execution_state(run, template)
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
        _record_workflow_run_state(run_state, status="resumed", details={"current_step_id": current_step_id})
    else:
        _emit_workflow_event("workflow.run.started", run["workflow_id"], run_id, payload={"current_step_id": current_step_id})
        _record_workflow_run_state(run_state, status="started", details={"current_step_id": current_step_id, "total_steps": total_steps})
        _record_workflow_total_steps(run, run_id=run_id, total_steps=total_steps)

    while current_step_id:
        step = steps_by_id.get(current_step_id)
        if not step:
            error = {"message": f"Workflow step '{current_step_id}' not found"}
            failed_run = _update_workflow_run(
                run_id,
                status="failed",
                current_step_id=current_step_id,
                error=error,
                finished_at=_now(),
            )
            _record_workflow_run_state(failed_run, status="failed", details={"current_step_id": current_step_id, "reason": "step_not_found"})
            _emit_workflow_event("workflow.step.failed", run["workflow_id"], run_id, payload={"step_id": current_step_id, "error": error["message"]})
            _emit_workflow_event("workflow.run.completed", run["workflow_id"], run_id, payload={"ok": False, "status": "failed"})
            return failed_run

        step_index = _workflow_step_index(current_step_id, ordered_ids, step_results)
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
        except SandboxPolicyError as exc:
            step_result = {
                "ok": False,
                "error": str(exc),
                "sandbox_reason": exc.reason,
                "sandbox_details": exc.details,
                "raw": {"ok": False, "error": str(exc)},
            }
        except Exception as exc:
            step_result = {
                "ok": False,
                "error": str(exc),
                "raw": {"ok": False, "error": str(exc)},
            }
        step_duration_ms = int((time.monotonic() - step_started) * 1000)

        save_key = str(step.get("save_as") or current_step_id)
        step_results[save_key] = step_result
        success = bool(step_result.get("ok"))
        next_step_id = _resolve_next_step(step, success=success)
        _record_workflow_step_state(
            step,
            workflow_id=run["workflow_id"],
            run_id=run_id,
            step_id=current_step_id,
            step_result=step_result,
            step_index=step_index,
            step_duration_ms=step_duration_ms,
            next_step_id=next_step_id,
            step_label=step_label,
        )

        if success:
            _emit_workflow_event("workflow.step.completed", run["workflow_id"], run_id, payload={"step_id": current_step_id, "save_as": save_key, "next_step_id": next_step_id or None})
        else:
            _emit_workflow_event("workflow.step.failed", run["workflow_id"], run_id, payload={"step_id": current_step_id, "error": step_result.get("error", ""), "next_step_id": next_step_id or None})

        should_pause = bool(step.get("pause_after")) or bool(step_result.get("pause_requested"))
        if should_pause and success and next_step_id:
            paused_run = _update_workflow_run(
                run_id,
                status="paused",
                current_step_id=next_step_id,
                step_results=step_results,
                pending_steps=[next_step_id],
                requested_pause=False,
            )
            _record_workflow_run_state(paused_run, status="paused", details={"current_step_id": next_step_id, "after_step_id": current_step_id})
            _emit_workflow_event("workflow.run.paused", run["workflow_id"], run_id, payload={"current_step_id": next_step_id})
            return paused_run

        if not success and not next_step_id:
            failed_run = _update_workflow_run(
                run_id,
                status="failed",
                current_step_id=current_step_id,
                step_results=step_results,
                pending_steps=[],
                error={"step_id": current_step_id, "message": step_result.get("error", "step failed")},
                finished_at=_now(),
            )
            _record_workflow_run_state(failed_run, status="failed", details={"current_step_id": current_step_id})
            _emit_workflow_event("workflow.run.completed", run["workflow_id"], run_id, payload={"ok": False, "status": "failed", "step_id": current_step_id})
            return failed_run

        if not next_step_id:
            completed_run = _update_workflow_run(
                run_id,
                status="completed",
                current_step_id="",
                step_results=step_results,
                pending_steps=[],
                error={},
                finished_at=_now(),
            )
            _record_workflow_run_state(completed_run, status="completed", details={"completed_from_step_id": current_step_id})
            _emit_workflow_event("workflow.run.completed", run["workflow_id"], run_id, payload={"ok": True, "status": "completed"})
            return completed_run

        current_step_id = next_step_id
        _update_workflow_run(
            run_id,
            status="running",
            current_step_id=current_step_id,
            step_results=step_results,
            pending_steps=[current_step_id],
            error={},
        )

    completed_run = _update_workflow_run(run_id, status="completed", current_step_id="", pending_steps=[], finished_at=_now())
    _record_workflow_run_state(completed_run, status="completed", details={"completed_from_step_id": ""})
    _emit_workflow_event("workflow.run.completed", run["workflow_id"], run_id, payload={"ok": True, "status": "completed"})
    return completed_run


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

    merged_context = dict(run.get("context", {}))
    merged_context.update(context_patch or {})
    _update_workflow_run(run_id, status="running", context=merged_context, error={}, requested_pause=False)
    return _execute_workflow_run(run_id, resume_event=True, progress_callback=progress_callback)


def cancel_workflow_run(run_id: str) -> dict[str, Any]:
    run = get_workflow_run(run_id)
    if not run:
        raise ValueError(f"Workflow run '{run_id}' not found")
    if run["status"] in {"completed", "failed", "cancelled"}:
        raise ValueError("Terminal workflow runs cannot be cancelled")

    cancelled = _update_workflow_run(run_id, status="cancelled", pending_steps=[], finished_at=_now())
    _record_workflow_run_state(cancelled, status="cancelled", details={"current_step_id": cancelled.get("current_step_id", "")})
    _emit_workflow_event("workflow.run.cancelled", cancelled["workflow_id"], run_id, payload={"status": "cancelled"})
    return cancelled




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
