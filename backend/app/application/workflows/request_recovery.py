"""Crash recovery for durable Workflow UI requests."""
from __future__ import annotations

from pathlib import Path

from app.application.workflows.store import (
    finalize_workflow_request,
    get_workflow_run,
    get_workflow_template,
    list_workflow_requests,
    mark_workflow_request_reconciliation,
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
_CODE_AGENT_TRIGGER_SOURCE = "code_agent_stream"
_TERMINAL_RUN_STATUSES = {"completed", "partial", "failed", "cancelled"}


def recover_incomplete_requests(*, db_path: str | Path) -> int:
    """Recover request claims left behind by a terminated backend process."""
    # Composer requests rendezvous with an in-memory code-agent generator. After
    # restart that generator no longer exists, so its card cannot be resumed.
    actionable, _ = list_workflow_requests(
        db_path=db_path,
        status="actionable",
        limit=10_000,
        offset=0,
    )
    recovered = 0
    for request in actionable:
        run = get_workflow_run(db_path=db_path, run_id=str(request["run_id"]))
        if not run or str(run.get("trigger_source") or "") != _CODE_AGENT_TRIGGER_SOURCE:
            continue
        finalize_workflow_request(
            db_path=db_path,
            request_id=str(request["request_id"]),
            status="cancelled",
            action="cancel",
        )
        update_workflow_run(
            db_path=db_path,
            run_id=str(run["run_id"]),
            status="cancelled",
            current_step_id="",
            pending_steps=[],
            error={"message": "backend restarted while the agent awaited Workflow UI"},
            finished_at=now_utc(),
        )
        recovered += 1

    requests, _ = list_workflow_requests(
        db_path=db_path,
        status="resolving",
        limit=10_000,
        offset=0,
    )
    for request in requests:
        run = get_workflow_run(db_path=db_path, run_id=str(request["run_id"]))
        if not run:
            finalize_workflow_request(
                db_path=db_path,
                request_id=str(request["request_id"]),
                status="cancelled",
                action="cancel",
            )
            recovered += 1
            continue
        if str(run.get("trigger_source") or "") == _CODE_AGENT_TRIGGER_SOURCE:
            finalize_workflow_request(
                db_path=db_path,
                request_id=str(request["request_id"]),
                status="cancelled",
                action="cancel",
            )
            update_workflow_run(
                db_path=db_path,
                run_id=str(run["run_id"]),
                status="cancelled",
                current_step_id="",
                pending_steps=[],
                error={
                    "message": "backend restarted while resolving a Workflow UI request"
                },
                finished_at=now_utc(),
            )
            recovered += 1
            continue
        if run.get("status") in _TERMINAL_RUN_STATUSES:
            run_cancelled = run.get("status") == "cancelled"
            finalize_workflow_request(
                db_path=db_path,
                request_id=str(request["request_id"]),
                status="cancelled" if run_cancelled else "resolved",
                action="cancel" if run_cancelled else "accept",
            )
            recovered += 1
            continue

        run_status = str(run.get("status", ""))
        safe_to_replay = run_status in set(_WAITING_STATUS.values())
        if not safe_to_replay:
            template = get_workflow_template(
                db_path=db_path,
                workflow_id=str(request["workflow_id"]),
            )
            steps = (template or {}).get("graph", {}).get("steps", [])
            step = next(
                (
                    item
                    for item in steps
                    if isinstance(item, dict)
                    and str(item.get("id", "")) == str(request["step_id"])
                ),
                {},
            )
            safe_to_replay = str(step.get("type", "")) == "request"

        if safe_to_replay:
            release_workflow_request_claim(
                db_path=db_path,
                request_id=str(request["request_id"]),
            )
            update_workflow_run(
                db_path=db_path,
                run_id=str(run["run_id"]),
                status=_WAITING_STATUS[str(request["kind"])],
                current_step_id=str(request["step_id"]),
                pending_steps=[str(request["step_id"])],
            )
        else:
            mark_workflow_request_reconciliation(
                db_path=db_path,
                request_id=str(request["request_id"]),
            )
            update_workflow_run(
                db_path=db_path,
                run_id=str(run["run_id"]),
                status="needs_reconciliation",
                current_step_id=str(request["step_id"]),
                pending_steps=[str(request["step_id"])],
            )
        recovered += 1
    return recovered
