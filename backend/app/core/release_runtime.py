"""Lifecycle handshake with the external release supervisor, not an executor."""
from __future__ import annotations

import hmac
from contextlib import closing
import os
import sqlite3
import threading
from typing import Callable

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse


_lock = threading.RLock()
_draining = os.getenv("ELIRA_RELEASE_STAGING") == "1"
_active_requests = 0
_agent_runs: set[str] = set()
_admitted = not _draining
_activate: Callable[[], None] | None = None
_shutdown: Callable[[], None] | None = None
CONTROL_PATH = "/api/release/control"
STATUS_PATH = "/api/release/status"


def set_callbacks(*, activate: Callable[[], None] | None = None,
                  shutdown: Callable[[], None] | None = None) -> None:
    global _activate, _shutdown
    if activate is not None:
        _activate = activate
    if shutdown is not None:
        _shutdown = shutdown


def is_staging() -> bool:
    return not _admitted


def begin_agent_run(run_id: str) -> None:
    with _lock:
        if _draining:
            raise RuntimeError("Elira is switching releases; retry the task after restart")
        _agent_runs.add(run_id)


def end_agent_run(run_id: str) -> None:
    with _lock:
        _agent_runs.discard(run_id)


class ReleaseDrainMiddleware:
    """Count request lifetimes; GET handlers can also mutate application state."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        global _active_requests
        if (scope["type"] != "http" or scope.get("method") == "OPTIONS"
                or scope.get("path") in {"/health", CONTROL_PATH}
                or (scope.get("method") == "GET" and scope.get("path") == STATUS_PATH)):
            await self.app(scope, receive, send)
            return
        # This observer cannot create work. Its perpetual SSE response must not
        # prevent an otherwise idle backend from draining; new observers still
        # receive 503 once draining begins.
        counted = not (scope.get("method") == "GET"
                       and scope.get("path") == "/api/agent-os/events/stream")
        with _lock:
            blocked = _draining
            if not blocked and counted:
                _active_requests += 1
        if blocked:
            await JSONResponse(
                {"detail": "Elira is switching releases; retry after restart."},
                status_code=503, headers={"Retry-After": "2"},
            )(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            if counted:
                with _lock:
                    _active_requests -= 1


def health_fields() -> dict:
    with _lock:
        return {"release_id": os.getenv("ELIRA_RELEASE_ID", "development"),
                "instance_id": os.getenv("ELIRA_RELEASE_INSTANCE", ""),
                "draining": _draining, "admitted": _admitted,
                "active_requests": _active_requests, "active_agent_runs": len(_agent_runs)}


def _workflow_busy() -> bool:
    from app.application.skill_services.telegram_runtime import telegram_bot_status
    telegram = telegram_bot_status()
    if telegram.get("running") or telegram.get("stopping"):
        return True
    from app.application.code_agent.tools._run import tracked_background_processes
    if any(item.get("status") == "running"
           for item in tracked_background_processes()):
        return True
    from app.application.workflows.db_path import get_workflow_db_path
    path = get_workflow_db_path()
    from pathlib import Path
    if not Path(path).exists():
        return False
    # Read-only observation: never rewrite an abandoned run to make drain pass.
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        return bool(db.execute(
            "SELECT 1 FROM workflow_runs WHERE status NOT IN "
            "('completed','partial','failed','cancelled') LIMIT 1"
        ).fetchone())


async def control(request: Request) -> dict:
    global _draining, _admitted
    expected = os.getenv("ELIRA_RELEASE_TOKEN", "")
    supplied = request.headers.get("x-elira-release-token", "")
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(403, "Release supervisor authentication required")
    payload = await request.json()
    action = payload.get("action") if isinstance(payload, dict) else None
    from app.application.workflows import triggers

    if action == "drain":
        triggers.stop_scheduler()
        # stop_scheduler cancels future ticks. An already running tick may still
        # have queued work; it is included in scheduler_status and durable runs.
        with _lock:
            idle = _active_requests == 0 and not _agent_runs
            if idle:
                # No admitted producer remains. Freeze new admission before the
                # background snapshot, so a just-completed agent cannot create
                # a job between that snapshot and the idle decision.
                was_draining = _draining
                _draining = True
                status = triggers.scheduler_status()
                try:
                    idle = not (status["active_trigger_ids"] or status["tick_in_progress"]
                                or _workflow_busy())
                except sqlite3.Error:
                    idle = False
                if not idle:
                    _draining = was_draining
        return {"ok": True, "idle": idle, **health_fields()}
    if action in {"activate", "resume"}:
        if action == "activate" and not _admitted and _activate is not None:
            _activate()
        with _lock:
            _admitted = True
            _draining = False
        if "pytest" not in __import__("sys").modules:
            triggers.start_scheduler()
        return {"ok": True, **health_fields()}
    if action == "shutdown":
        with _lock:
            idle = _draining and _active_requests == 0 and not _agent_runs
        if not idle or _shutdown is None:
            raise HTTPException(409, "Backend must be drained by its supervisor first")
        _shutdown()
        return {"ok": True}
    raise HTTPException(400, "Unknown release lifecycle action")
