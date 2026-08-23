"""Interval triggers owned by the existing Workflow runtime.

The old autopipeline control plane is intentionally not revived. A trigger only
starts an existing Workflow template and inherits one of the three Workflow UI
permission modes. Persistence stays in ``workflow_engine.db``.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.application.workflows.db_path import get_workflow_db_path
from app.application.workflows.runtime import start_workflow_run
from app.application.workflows.store import (
    claim_workflow_trigger,
    finish_workflow_trigger_run,
    get_workflow_run,
    init_db,
    list_due_workflow_triggers,
    list_workflow_triggers,
)


logger = logging.getLogger(__name__)

_TICK_SECONDS = 30.0
_lock = threading.RLock()
_timer: threading.Timer | None = None
_running = False
_active_trigger_ids: set[str] = set()
_TERMINAL_RUN_STATUSES = {"completed", "partial", "failed", "cancelled"}


def _db_path(db_path: str | Path | None = None) -> str | Path:
    resolved = db_path or get_workflow_db_path()
    init_db(db_path=resolved)
    return resolved


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_message(run: dict[str, Any]) -> str:
    error = run.get("error", {})
    if isinstance(error, dict):
        return str(error.get("message") or error.get("error") or "")
    return str(error or "")


def _execute_trigger(trigger: dict[str, Any], db_path: str | Path) -> None:
    trigger_id = str(trigger.get("id") or "")
    run_id = ""
    created_run_id = [""]
    try:
        run = start_workflow_run(
            workflow_id=str(trigger.get("workflow_id") or ""),
            workflow_input=(
                dict(trigger.get("input"))
                if isinstance(trigger.get("input"), dict)
                else {}
            ),
            context=(
                dict(trigger.get("context"))
                if isinstance(trigger.get("context"), dict)
                else {}
            ),
            trigger_source=f"workflow_trigger:{trigger_id}",
            permission_mode=str(trigger.get("permission_mode") or "ask"),
            run_created_callback=lambda value: created_run_id.__setitem__(
                0, str(value or "")
            ),
            db_path=db_path,
        )
        run_id = str(run.get("run_id") or created_run_id[0])
        finish_workflow_trigger_run(
            db_path=db_path,
            trigger_id=trigger_id,
            run_id=run_id,
            status=str(run.get("status") or "failed"),
            error=_error_message(run),
        )
    except Exception as exc:
        logger.exception("workflow trigger %s failed", trigger_id)
        finish_workflow_trigger_run(
            db_path=db_path,
            trigger_id=trigger_id,
            run_id=run_id or created_run_id[0],
            status="failed",
            error=str(exc),
        )
    finally:
        with _lock:
            _active_trigger_ids.discard(trigger_id)

def run_due_triggers(*, db_path: str | Path | None = None) -> int:
    resolved = _db_path(db_path)
    due_at = _utc_now()
    started = 0
    for candidate in list_due_workflow_triggers(db_path=resolved, due_at=due_at):
        trigger_id = str(candidate.get("id") or "")
        previous_run_id = str(candidate.get("last_run_id") or "")
        if previous_run_id:
            previous_run = get_workflow_run(
                db_path=resolved,
                run_id=previous_run_id,
            )
            if previous_run and str(previous_run.get("status") or "") not in (
                _TERMINAL_RUN_STATUSES
            ):
                continue
        with _lock:
            if trigger_id in _active_trigger_ids:
                continue
            claimed, did_claim = claim_workflow_trigger(
                db_path=resolved,
                trigger_id=trigger_id,
                due_at=due_at,
            )
            if not did_claim or not claimed:
                continue
            _active_trigger_ids.add(trigger_id)
        threading.Thread(
            target=_execute_trigger,
            args=(claimed, resolved),
            name=f"workflow-trigger-{trigger_id}",
            daemon=True,
        ).start()
        started += 1
    return started


def _tick() -> None:
    global _timer
    try:
        run_due_triggers()
    except Exception:
        logger.exception("workflow trigger tick failed")
    with _lock:
        if not _running:
            _timer = None
            return
        _timer = threading.Timer(_TICK_SECONDS, _tick)
        _timer.daemon = True
        _timer.start()


def start_scheduler() -> dict[str, Any]:
    global _running, _timer
    with _lock:
        if _running:
            return scheduler_status()
        _running = True
        _timer = threading.Timer(0.0, _tick)
        _timer.daemon = True
        _timer.start()
    return scheduler_status()


def stop_scheduler() -> dict[str, Any]:
    global _running, _timer
    with _lock:
        _running = False
        if _timer is not None:
            _timer.cancel()
            _timer = None
    return scheduler_status()


def scheduler_status(*, db_path: str | Path | None = None) -> dict[str, Any]:
    triggers, total = list_workflow_triggers(db_path=_db_path(db_path))
    with _lock:
        running = _running
        active = sorted(_active_trigger_ids)
    return {
        "running": running,
        "tick_seconds": _TICK_SECONDS,
        "trigger_count": total,
        "enabled_count": sum(1 for item in triggers if item.get("enabled")),
        "active_trigger_ids": active,
    }
