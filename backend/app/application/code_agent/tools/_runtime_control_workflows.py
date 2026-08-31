"""Workflow template, run, trigger, and scheduler runtime_control operations."""
from __future__ import annotations

from typing import Any

from app.application.code_agent.tools._runtime_control_contract import (
    input_request,
    require_id,
)


def workflow_control(
    operation: str,
    workflow_id: str,
    run_id: str,
    trigger_id: str,
    permission_mode: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    from app.application.workflows.db_path import get_workflow_db_path
    from app.application.workflows import runtime, store, triggers

    db_path = get_workflow_db_path()
    store.init_db(db_path=db_path)
    if operation == "workflow_list":
        items, total = store.list_workflow_templates(
            db_path=db_path,
            include_disabled=bool(config.get("include_disabled", True)),
            source=str(config.get("source") or "").strip() or None,
        )
        return {"ok": True, "workflows": items, "total": total}
    if operation == "workflow_upsert":
        template = config.get("template", config)
        if not isinstance(template, dict) or not template:
            raise ValueError("config.template must contain a Workflow template")
        candidate = dict(template)
        if workflow_id:
            candidate["id"] = workflow_id
        return {
            "ok": True,
            "workflow": store.upsert_workflow_template(
                db_path=db_path,
                template=candidate,
            )
        }
    if operation == "workflow_remove":
        wid = require_id(
            workflow_id or str(config.get("workflow_id") or ""),
            "workflow_id",
            "ID Workflow",
        )
        return {
            "ok": True,
            **store.delete_workflow_template(db_path=db_path, workflow_id=wid),
        }
    if operation == "workflow_run":
        wid = require_id(
            workflow_id or str(config.get("workflow_id") or ""),
            "workflow_id",
            "ID Workflow",
        )
        workflow_input = config.get("input", {})
        context = config.get("context", {})
        if not isinstance(workflow_input, dict) or not isinstance(context, dict):
            raise ValueError("config.input and config.context must be objects")
        run = runtime.start_workflow_run_background(
            workflow_id=wid,
            workflow_input=workflow_input,
            context=context,
            trigger_source="runtime_control",
            permission_mode=permission_mode,
            db_path=db_path,
        )
        return {"ok": True, "run": run}
    if operation == "workflow_runs":
        items, total = store.list_workflow_runs(
            db_path=db_path,
            workflow_id=(
                workflow_id or str(config.get("workflow_id") or "").strip() or None
            ),
            status=str(config.get("status") or "").strip() or None,
            limit=max(1, int(config.get("limit") or 100)),
            offset=max(0, int(config.get("offset") or 0)),
        )
        return {"ok": True, "runs": items, "total": total}
    if operation == "workflow_resume":
        rid = require_id(
            run_id or str(config.get("run_id") or ""),
            "run_id",
            "ID запуска Workflow",
        )
        patch = config.get("context_patch", {})
        if not isinstance(patch, dict):
            raise ValueError("config.context_patch must be an object")
        return {
            "ok": True,
            "run": runtime.resume_workflow_run(
                rid,
                context_patch=patch,
                db_path=db_path,
            )
        }
    if operation == "workflow_cancel":
        rid = require_id(
            run_id or str(config.get("run_id") or ""),
            "run_id",
            "ID запуска Workflow",
        )
        return {
            "ok": True,
            "run": runtime.cancel_workflow_run(rid, db_path=db_path),
        }
    if operation == "workflow_trigger_list":
        enabled = config.get("enabled")
        items, total = store.list_workflow_triggers(
            db_path=db_path,
            enabled=bool(enabled) if isinstance(enabled, bool) else None,
        )
        return {
            "ok": True,
            "triggers": items,
            "total": total,
            "scheduler": triggers.scheduler_status(db_path=db_path),
        }
    if operation == "workflow_trigger_upsert":
        trigger = config.get("trigger", config)
        if not isinstance(trigger, dict):
            raise ValueError("config.trigger must be an object")
        candidate = dict(trigger)
        if trigger_id:
            candidate["id"] = trigger_id
        if workflow_id:
            candidate["workflow_id"] = workflow_id
        candidate["permission_mode"] = permission_mode
        if not candidate.get("workflow_id"):
            raise input_request(
                "Выберите Workflow для расписания.",
                "workflow_id",
                "ID Workflow",
            )
        return {
            "ok": True,
            "trigger": store.upsert_workflow_trigger(
                db_path=db_path,
                trigger=candidate,
            )
        }
    if operation == "workflow_trigger_remove":
        tid = require_id(
            trigger_id or str(config.get("trigger_id") or ""),
            "trigger_id",
            "ID триггера",
        )
        return {
            "ok": True,
            **store.delete_workflow_trigger(db_path=db_path, trigger_id=tid),
        }
    if operation == "workflow_scheduler_status":
        return {"ok": True, "scheduler": triggers.scheduler_status(db_path=db_path)}
    if operation == "workflow_scheduler_start":
        return {"ok": True, "scheduler": triggers.start_scheduler()}
    if operation == "workflow_scheduler_stop":
        return {"ok": True, "scheduler": triggers.stop_scheduler()}
    raise ValueError(f"unsupported Workflow operation: {operation}")
