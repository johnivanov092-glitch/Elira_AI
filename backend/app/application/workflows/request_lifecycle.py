from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.workflows.events import emit_workflow_event
from app.application.workflows.request_recovery import recover_incomplete_requests
from app.application.workflows.request_validation import validate_resolution_values
from app.application.workflows.step_results import WorkflowRequestSpec
from app.application.workflows.store import (
    claim_workflow_request,
    create_workflow_request_record,
    create_runtime_workflow_run_record,
    finalize_workflow_request,
    get_workflow_request,
    get_workflow_run,
    list_workflow_requests,
    now_utc,
    release_workflow_request_claim,
    update_workflow_run,
)


_WAITING_STATUS = {
    "input": "needs_input",
    "secret": "needs_secret",
    "elevation": "needs_elevation",
    "approval": "waiting_approval",
}
_TERMINAL_REQUEST_STATUSES = {"resolved", "declined", "cancelled", "expired"}
_RESOLUTIONS_CONTEXT_KEY = "_workflow_request_resolutions"
_CODE_AGENT_WORKFLOW_ID = "builtin-composer-runtime"
_CODE_AGENT_TRIGGER_SOURCE = "code_agent_stream"


def execute_request_step(
    step: dict[str, Any],
    run_context: dict[str, Any],
    permission_mode: str,
) -> dict[str, Any]:
    config = step.get("config", {}) or {}
    kind = str(config.get("kind", "")).strip()
    if kind == "approval" and permission_mode == "bypass":
        return {
            "ok": True,
            "action": "bypass",
            "values": {},
            "raw": {"ok": True, "action": "bypass", "values": {}},
        }

    resolutions = run_context.get(_RESOLUTIONS_CONTEXT_KEY, {})
    if isinstance(resolutions, dict):
        resolution = resolutions.get(str(step.get("id", "")))
        if isinstance(resolution, dict):
            values = resolution.get("values", {})
            safe_values = values if isinstance(values, dict) else {}
            return {
                "ok": True,
                "request_id": str(resolution.get("request_id", "")),
                "action": str(resolution.get("action", "accept")),
                "values": safe_values,
                "raw": {
                    "ok": True,
                    "request_id": str(resolution.get("request_id", "")),
                    "action": str(resolution.get("action", "accept")),
                    "values": safe_values,
                },
            }

    status = "waiting_approval" if kind == "approval" else f"needs_{kind}"
    request = {
        "kind": kind,
        "message": str(config.get("message", "")).strip(),
        "schema": (
            config.get("schema", {})
            if isinstance(config.get("schema", {}), dict)
            else {}
        ),
        "sensitive": kind == "secret" or bool(config.get("sensitive", False)),
    }
    raw = {"ok": False, "status": status, "request": request}
    return {**raw, "raw": raw}


def pause_workflow_for_request(
    *,
    db_path: str | Path,
    run: dict[str, Any],
    step_id: str,
    request_spec: WorkflowRequestSpec,
) -> dict[str, Any]:
    request = create_workflow_request_record(
        db_path=db_path,
        workflow_id=str(run["workflow_id"]),
        run_id=str(run["run_id"]),
        step_id=step_id,
        kind=request_spec.kind,
        message=request_spec.message,
        schema=request_spec.schema,
        sensitive=request_spec.sensitive,
        provider_ref=request_spec.provider_ref,
    )
    waiting_status = _WAITING_STATUS[request_spec.kind]
    waiting_run = update_workflow_run(
        db_path=db_path,
        run_id=str(run["run_id"]),
        status=waiting_status,
        current_step_id=step_id,
        pending_steps=[step_id],
        error={},
        requested_pause=False,
    )
    if waiting_run.get("status") != waiting_status:
        finalize_workflow_request(
            db_path=db_path,
            request_id=str(request["request_id"]),
            status="cancelled",
            action="cancel",
        )
        return waiting_run

    from app.application.workflows.execution import record_workflow_run_state

    record_workflow_run_state(
        waiting_run,
        status=waiting_status,
        details={
            "current_step_id": step_id,
            "request_id": request["request_id"],
            "request_kind": request_spec.kind,
        },
    )
    emit_workflow_event(
        "item/request",
        str(run["workflow_id"]),
        str(run["run_id"]),
        payload={
            "request_id": request["request_id"],
            "step_id": step_id,
            "kind": request_spec.kind,
            "status": "pending",
            "message": request_spec.message,
            "schema": request_spec.schema,
            "sensitive": request_spec.sensitive,
        },
    )
    emit_workflow_event(
        "workflow/waiting",
        str(run["workflow_id"]),
        str(run["run_id"]),
        payload={
            "request_id": request["request_id"],
            "step_id": step_id,
            "kind": request_spec.kind,
            "status": waiting_status,
        },
    )
    return waiting_run


def start_code_agent_workflow_run(
    *,
    db_path: str | Path,
    workflow_run_id: str,
    code_agent_run_id: str,
    permission_mode: str,
    workflow_root_run_id: str | None = None,
    attempt_number: int = 1,
) -> dict[str, Any]:
    from app.application.workflows.store import init_db

    if attempt_number < 1:
        raise ValueError("Workflow attempt_number must be greater than zero")
    init_db(db_path=db_path)
    run = create_runtime_workflow_run_record(
        db_path=db_path,
        run_id=workflow_run_id,
        workflow_id=_CODE_AGENT_WORKFLOW_ID,
        context={
            "workflow_root_run_id": workflow_root_run_id or workflow_run_id,
            "attempt_number": attempt_number,
            "code_agent_run_id": code_agent_run_id,
        },
        trigger_source=_CODE_AGENT_TRIGGER_SOURCE,
        permission_mode=permission_mode,
    )
    if run.get("status") != "running":
        return run
    emit_workflow_event(
        "workflow.run.started",
        _CODE_AGENT_WORKFLOW_ID,
        workflow_run_id,
        payload={"status": "running", "code_agent_run_id": code_agent_run_id},
    )
    return run


def finish_code_agent_workflow_run(
    *,
    db_path: str | Path,
    workflow_run_id: str,
    done_event: dict[str, Any],
) -> dict[str, Any]:
    run = get_workflow_run(db_path=db_path, run_id=workflow_run_id)
    if not run:
        return {}
    if run.get("status") in {"completed", "partial", "failed", "cancelled"}:
        return run
    stop_reason = str(done_event.get("stop_reason") or "")
    completion_status = str(done_event.get("completion_status") or "none")
    if stop_reason == "cancelled":
        status = "cancelled"
    elif not bool(done_event.get("ok")) or completion_status == "failed":
        status = "failed"
    elif bool(done_event.get("partial")) or completion_status in {
        "partial",
        "unverified",
    }:
        status = "partial"
    else:
        status = "completed"
    pending, _ = list_workflow_requests(
        db_path=db_path,
        run_id=workflow_run_id,
        status="actionable",
        limit=10_000,
        offset=0,
    )
    for request in pending:
        finalize_workflow_request(
            db_path=db_path,
            request_id=str(request["request_id"]),
            status="cancelled",
            action="cancel",
        )
    finished = update_workflow_run(
        db_path=db_path,
        run_id=workflow_run_id,
        status=status,
        current_step_id="",
        pending_steps=[],
        error=(
            {"message": str(done_event.get("error") or stop_reason)}
            if status == "failed"
            else {}
        ),
        finished_at=now_utc(),
    )
    if finished.get("status") != status:
        return finished
    emit_workflow_event(
        "workflow.run.completed" if status != "cancelled" else "workflow.run.cancelled",
        str(run["workflow_id"]),
        workflow_run_id,
        payload={
            "status": status,
            "ok": bool(done_event.get("ok")),
            "completion_status": completion_status,
        },
    )
    return finished


def get_request(*, db_path: str | Path, request_id: str) -> dict[str, Any] | None:
    return get_workflow_request(db_path=db_path, request_id=request_id)


def list_requests(
    *,
    db_path: str | Path,
    run_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    return list_workflow_requests(
        db_path=db_path,
        run_id=run_id,
        status=status,
        limit=limit,
        offset=offset,
    )


def resolve_request(
    *,
    db_path: str | Path,
    request_id: str,
    action: str,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = get_workflow_request(db_path=db_path, request_id=request_id)
    if not request:
        raise ValueError(f"Workflow request '{request_id}' not found")
    run = get_workflow_run(db_path=db_path, run_id=str(request["run_id"]))
    if not run:
        raise ValueError(f"Workflow run '{request['run_id']}' not found")
    if request["status"] in _TERMINAL_REQUEST_STATUSES:
        return {"request": request, "run": run}
    if request["status"] not in {"pending", "needs_reconciliation"}:
        raise RuntimeError(f"Workflow request is already {request['status']}")

    safe_values = values if isinstance(values, dict) else {}
    if action == "accept":
        validate_resolution_values(request, safe_values)

    claimed_request, claimed = claim_workflow_request(
        db_path=db_path,
        request_id=request_id,
    )
    if not claimed:
        if claimed_request and claimed_request["status"] in _TERMINAL_REQUEST_STATUSES:
            current_run = get_workflow_run(
                db_path=db_path,
                run_id=str(claimed_request["run_id"]),
            )
            return {"request": claimed_request, "run": current_run or run}
        raise RuntimeError("Workflow request is already being resolved")
    assert claimed_request is not None

    try:
        if str(run.get("trigger_source") or "") == _CODE_AGENT_TRIGGER_SOURCE:
            provider_ref = str(claimed_request.get("provider_ref") or "").strip()
            from app.application.code_agent.agent_loop import (
                request_cancel,
                submit_workflow_response,
            )

            if action == "cancel":
                code_agent_run_id = str(
                    (run.get("context") or {}).get("code_agent_run_id") or ""
                )
                if code_agent_run_id:
                    request_cancel(code_agent_run_id)
            elif not submit_workflow_response(provider_ref, action, safe_values):
                raise RuntimeError("the code-agent request is no longer live")
            terminal_status = (
                "resolved" if action == "accept"
                else "declined" if action == "decline"
                else "cancelled"
            )
            finalized = finalize_workflow_request(
                db_path=db_path,
                request_id=request_id,
                status=terminal_status,
                action=action,
                resolution=safe_values,
            )
            next_run_status = "cancelled" if action == "cancel" else "running"
            updated_run = update_workflow_run(
                db_path=db_path,
                run_id=str(run["run_id"]),
                status=next_run_status,
                current_step_id="agent" if next_run_status == "running" else "",
                pending_steps=["agent"] if next_run_status == "running" else [],
                error={},
            )
            emit_workflow_event(
                "serverRequest/resolved",
                str(run["workflow_id"]),
                str(run["run_id"]),
                payload={
                    "request_id": request_id,
                    "step_id": request["step_id"],
                    "kind": request["kind"],
                    "action": action,
                    "status": terminal_status,
                },
            )
            return {"request": finalized or claimed_request, "run": updated_run}
        if action != "accept":
            from app.application.workflows.runtime import cancel_workflow_run

            terminal_status = "declined" if action == "decline" else "cancelled"
            finalized = finalize_workflow_request(
                db_path=db_path,
                request_id=request_id,
                status=terminal_status,
                action=action,
            )
            emit_workflow_event(
                "serverRequest/resolved",
                str(run["workflow_id"]),
                str(run["run_id"]),
                payload={
                    "request_id": request_id,
                    "step_id": request["step_id"],
                    "kind": request["kind"],
                    "action": action,
                    "status": terminal_status,
                },
            )
            cancelled = cancel_workflow_run(str(run["run_id"]), db_path=db_path)
            return {"request": finalized or claimed_request, "run": cancelled}

        context = dict(run.get("context", {}))
        resolutions = context.get(_RESOLUTIONS_CONTEXT_KEY, {})
        if not isinstance(resolutions, dict):
            resolutions = {}
        resolutions = dict(resolutions)
        previous_resolution = resolutions.get(str(request["step_id"]), {})
        previous_values = (
            previous_resolution.get("values", {})
            if isinstance(previous_resolution, dict)
            else {}
        )
        if not isinstance(previous_values, dict):
            previous_values = {}
        request_schema = request.get("schema", {})
        approved_tool = (
            request_schema.get("x-elira-tool")
            if isinstance(request_schema, dict)
            and isinstance(request_schema.get("x-elira-tool"), dict)
            and str(request.get("kind") or "") == "approval"
            else None
        )
        resolutions[str(request["step_id"])] = {
            "request_id": request_id,
            "action": action,
            "values": {**previous_values, **safe_values},
            **({"approved_tool": approved_tool} if approved_tool else {}),
        }
        context[_RESOLUTIONS_CONTEXT_KEY] = resolutions
        resumed_run = update_workflow_run(
            db_path=db_path,
            run_id=str(run["run_id"]),
            status="running",
            current_step_id=str(request["step_id"]),
            context=context,
            pending_steps=[str(request["step_id"])],
            error={},
        )
        if resumed_run.get("status") != "running":
            finalized = finalize_workflow_request(
                db_path=db_path,
                request_id=request_id,
                status="cancelled",
                action="cancel",
            )
            return {"request": finalized or claimed_request, "run": resumed_run}
        emit_workflow_event(
            "serverRequest/resolved",
            str(run["workflow_id"]),
            str(run["run_id"]),
            payload={
                "request_id": request_id,
                "step_id": request["step_id"],
                "kind": request["kind"],
                "action": action,
                "status": "resolved",
            },
        )

        from app.application.workflows.runtime import execute_workflow_run

        resumed = execute_workflow_run(
            run_id=str(run["run_id"]),
            db_path=db_path,
            resume_event=True,
        )
        finalized = finalize_workflow_request(
            db_path=db_path,
            request_id=request_id,
            status="resolved",
            action=action,
            resolution=safe_values,
        )
        return {"request": finalized or claimed_request, "run": resumed}
    except Exception:
        release_workflow_request_claim(db_path=db_path, request_id=request_id)
        current = get_workflow_run(db_path=db_path, run_id=str(run["run_id"])) or run
        if current.get("status") == "running":
            update_workflow_run(
                db_path=db_path,
                run_id=str(run["run_id"]),
                status=_WAITING_STATUS[str(request["kind"])],
                current_step_id=str(request["step_id"]),
                pending_steps=[str(request["step_id"])],
            )
        raise
