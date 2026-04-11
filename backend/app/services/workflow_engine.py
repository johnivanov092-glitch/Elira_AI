"""Legacy workflow-engine monolith kept for compatibility during extraction."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
from app.services.agent_monitor import (
    WORKFLOW_ENGINE_AGENT_ID,
    record_resource_usage,
    record_workflow_run_metric,
    record_workflow_step_metric,
)
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
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _workflow_duration_ms(run: dict[str, Any]) -> int:
    started_at = _parse_dt(str(run.get("started_at", "")))
    if not started_at:
        return 0
    return max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))


def _record_workflow_run_state(run: dict[str, Any], *, status: str, details: dict[str, Any] | None = None) -> None:
    try:
        record_workflow_run_metric(
            workflow_id=str(run.get("workflow_id", "")),
            run_id=str(run.get("run_id", "")),
            status=status,
            duration_ms=_workflow_duration_ms(run),
            details=details or {},
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


def _run_state_for_execution(run: dict[str, Any], template: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    graph = template.get("graph", {})
    steps = graph.get("steps", []) if isinstance(graph, dict) else []
    steps_by_id = {str(step.get("id")): step for step in steps}
    ordered_ids = [str(step.get("id")) for step in steps]
    return graph, steps_by_id, ordered_ids


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

    _, steps_by_id, ordered_ids = _run_state_for_execution(run, template)
    total_steps = len(ordered_ids)
    workflow_input = run.get("input", {})
    context = run.get("context", {})
    step_results = run.get("step_results", {})
    current_step_id = str(run.get("current_step_id", "")).strip()
    run_state = get_workflow_run(run_id) or run

    if resume_event:
        _emit_workflow_event("workflow.run.resumed", run["workflow_id"], run_id, payload={"current_step_id": current_step_id})
        _record_workflow_run_state(run_state, status="resumed", details={"current_step_id": current_step_id})
    else:
        _emit_workflow_event("workflow.run.started", run["workflow_id"], run_id, payload={"current_step_id": current_step_id})
        _record_workflow_run_state(run_state, status="started", details={"current_step_id": current_step_id, "total_steps": total_steps})
        try:
            record_resource_usage(
                agent_id=WORKFLOW_ENGINE_AGENT_ID,
                run_id=run_id,
                workflow_id=run["workflow_id"],
                resource="workflow_total_steps",
                amount=total_steps,
                unit="count",
                details={"trigger_source": run.get("trigger_source", "api")},
            )
        except Exception:
            pass

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

        step_index = ordered_ids.index(current_step_id) + 1 if current_step_id in ordered_ids else len(step_results) + 1
        if progress_callback:
            progress_callback(step_index, total_steps, _step_label(step))

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
        step_agent_id = str(step.get("agent_id", "")).strip() if step.get("type") == "agent" else WORKFLOW_ENGINE_AGENT_ID
        step_details = {
            "label": _step_label(step),
            "index": step_index,
            "next_step_id": next_step_id or None,
        }
        if step.get("type") == "tool":
            step_details["tool_name"] = str(step.get("tool_name", "")).strip()
        if step_result.get("error"):
            step_details["error"] = str(step_result.get("error", ""))[:500]
        if step_result.get("sandbox_reason"):
            step_details["sandbox_reason"] = step_result.get("sandbox_reason")
        try:
            record_workflow_step_metric(
                agent_id=step_agent_id,
                workflow_id=run["workflow_id"],
                run_id=run_id,
                step_id=current_step_id,
                step_type=str(step.get("type", "")),
                ok=success,
                duration_ms=step_duration_ms,
                details=step_details,
            )
            record_resource_usage(
                agent_id=step_agent_id,
                run_id=run_id,
                workflow_id=run["workflow_id"],
                step_id=current_step_id,
                resource="step_duration",
                amount=step_duration_ms,
                unit="ms",
                details={"step_type": str(step.get("type", "")), "label": _step_label(step)},
            )
        except Exception:
            pass

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
