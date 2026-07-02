from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime

from app.application.task_planner import runtime as planner_runtime
from app.core.config import DATA_DIR
from app.infrastructure.db.connection import connect_sqlite


logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "task_planner.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _connect():
    # WAL (connect_sqlite default) so plan writes don't block readers.
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row)


def _init_db():
    planner_runtime.init_db(connect_func=_connect)


_init_db()

PRIORITIES = ["low", "medium", "high", "urgent"]
STATUSES = ["todo", "in_progress", "done", "cancelled"]


def create_task(
    title: str,
    description: str = "",
    category: str = "general",
    priority: str = "medium",
    due_date: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    return planner_runtime.create_task(
        connect_func=_connect,
        id_func=lambda: str(uuid.uuid4())[:8],
        now_func=lambda: datetime.utcnow().isoformat(),
        title=title,
        description=description,
        category=category,
        priority=priority,
        due_date=due_date,
        tags=tags,
    )


def list_tasks(status: str | None = None, category: str | None = None, limit: int = 100) -> dict:
    return planner_runtime.list_tasks(
        connect_func=_connect,
        status=status,
        category=category,
        limit=limit,
    )


def get_task(tid: str) -> dict:
    return planner_runtime.get_task(connect_func=_connect, tid=tid)


def update_task(tid: str, **kwargs) -> dict:
    return planner_runtime.update_task(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
        tid=tid,
        **kwargs,
    )


def delete_task(tid: str) -> dict:
    return planner_runtime.delete_task(connect_func=_connect, tid=tid)


def task_stats() -> dict:
    return planner_runtime.task_stats(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
    )


def bump_retry(tid: str, backoff_base_seconds: int = 60) -> dict:
    return planner_runtime.bump_retry(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
        tid=tid,
        backoff_base_seconds=backoff_base_seconds,
    )


def set_waiting_approval(tid: str) -> dict:
    return planner_runtime.set_waiting_approval(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
        tid=tid,
    )


def recover_stale_tasks(
    *,
    stale_after_seconds: int = 3600,
    limit: int = 50,
    backoff_base_seconds: int = 60,
) -> dict:
    from app.application.event_bus.runtime import emit_event

    return planner_runtime.recover_stale_tasks(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
        stale_after_seconds=stale_after_seconds,
        limit=limit,
        backoff_base_seconds=backoff_base_seconds,
        emit_event_func=emit_event,
    )


# Daily gate for observability retention, piggy-backed on the recovery loop so
# VACUUM runs ~once/day instead of every 600s interval. monotonic() starts at 0,
# so the first loop iteration (after one interval) runs the initial prune.
_last_observability_prune: list[float] = [0.0]
_OBSERVABILITY_PRUNE_EVERY_S = 86400.0


def _maybe_prune_observability() -> None:
    """Best-effort daily retention for agent_monitor.db + event_bus.db (FIX-10).
    Lazy-imports the runtimes so task_planner stays decoupled; never raises."""
    import time as _time

    now = _time.monotonic()
    if now - _last_observability_prune[0] < _OBSERVABILITY_PRUNE_EVERY_S:
        return
    _last_observability_prune[0] = now
    try:
        from app.application.monitoring.runtime import prune_metrics
        prune_metrics()
    except Exception as exc:
        logger.warning("metrics retention failed: %s", exc)
    try:
        from app.application.event_bus.runtime import prune_events
        prune_events()
    except Exception as exc:
        logger.warning("event retention failed: %s", exc)


def start_task_recovery_scheduler(
    *,
    interval_seconds: float | None = None,
    recover_fn=None,
    stop_event=None,
):
    """Start a daemon timer that periodically re-runs recover_stale_tasks.

    Startup recovery (main.py) only runs once; on a long-lived server a task
    that goes stale hours later would otherwise wait for the next restart. This
    re-runs recovery on an interval so the server self-heals. Best-effort: the
    thread is a daemon (dies with the process) and swallows/logs errors.

    interval_seconds defaults to env ``ELIRA_TASK_RECOVERY_INTERVAL_SECONDS``
    (floor 60s, default 600s). ``recover_fn`` is injectable for tests.
    ``stop_event`` (threading.Event) lets a caller/test stop the loop
    gracefully; without it the daemon runs until the process exits.
    Returns the started Thread.
    """
    import os
    import threading
    import time

    if interval_seconds is None:
        interval_seconds = max(60, int(os.getenv("ELIRA_TASK_RECOVERY_INTERVAL_SECONDS", "600")))
    fn = recover_fn or recover_stale_tasks

    def _loop() -> None:
        while True:
            # stop_event.wait returns True the instant it is set, so shutdown is
            # prompt instead of sleeping out the whole interval.
            if stop_event is not None:
                if stop_event.wait(interval_seconds):
                    return
            else:
                time.sleep(interval_seconds)
            try:
                fn()
            except Exception as exc:
                logger.warning("periodic task recovery failed: %s", exc)
            _maybe_prune_observability()

    thread = threading.Thread(target=_loop, name="task-recovery", daemon=True)
    thread.start()
    return thread


def list_checklist(run_id: str) -> dict:
    return planner_runtime.list_checklist(
        connect_func=_connect,
        run_id=run_id,
    )


def todo_update(
    *,
    run_id: str,
    items: list[dict] | None = None,
    updates: list[dict] | None = None,
) -> dict:
    from app.application.event_bus.runtime import emit_event

    return planner_runtime.update_checklist(
        connect_func=_connect,
        id_func=lambda: str(uuid.uuid4())[:8],
        now_func=lambda: datetime.utcnow().isoformat(),
        run_id=run_id,
        items=items,
        updates=updates,
        emit_event_func=emit_event,
    )


def start_subagent_run(
    *,
    parent_run_id: str,
    role: str,
    task: str,
    depth: int = 0,
    max_steps: int = 0,
    max_context_tokens: int = 0,
    tool_allowlist: list[str] | tuple[str, ...] | None = None,
) -> dict:
    from app.application.event_bus.runtime import emit_event

    return planner_runtime.start_subagent_run(
        connect_func=_connect,
        id_func=lambda: f"sub-{uuid.uuid4().hex}",
        now_func=lambda: datetime.utcnow().isoformat(),
        parent_run_id=parent_run_id,
        role=role,
        task=task,
        depth=depth,
        max_steps=max_steps,
        max_context_tokens=max_context_tokens,
        tool_allowlist=tool_allowlist,
        emit_event_func=emit_event,
    )


def finish_subagent_run(
    *,
    subagent_run_id: str,
    status: str,
    result_text: str = "",
    error: str = "",
) -> dict:
    from app.application.event_bus.runtime import emit_event

    return planner_runtime.finish_subagent_run(
        connect_func=_connect,
        now_func=lambda: datetime.utcnow().isoformat(),
        subagent_run_id=subagent_run_id,
        status=status,
        result_text=result_text,
        error=error,
        emit_event_func=emit_event,
    )


def get_subagent_run(subagent_run_id: str) -> dict:
    return planner_runtime.get_subagent_run(
        connect_func=_connect,
        subagent_run_id=subagent_run_id,
    )


def list_subagent_runs(parent_run_id: str, limit: int = 100) -> dict:
    return planner_runtime.list_subagent_runs(
        connect_func=_connect,
        parent_run_id=parent_run_id,
        limit=limit,
    )
