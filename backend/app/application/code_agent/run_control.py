"""Ownership of live runs, inference cancellation and Workflow rendezvous.

Durable Workflow requests and session-level Stop remain with their existing
owners. This module owns only the active generator's registries and cleanup.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generator
from weakref import WeakValueDictionary

from app.application.code_agent.loop_helpers import (
    _WORKFLOW_REQUEST_KEEPALIVE_EVERY,
    _WORKFLOW_REQUEST_POLL_INTERVAL,
)
from app.infrastructure.llm.openai_compatible import LLMStreamCancelHandle

# Keep the operational log category used before this mechanical extraction.
logger = logging.getLogger("app.application.code_agent.agent_loop")

# Global registry of active cancel events so an external HTTP route can flip
# the flag mid-stream. Keys are run_ids handed back to the client.
_CANCEL_REGISTRY: dict[str, threading.Event] = {}
_UPSTREAM_HANDLE_REGISTRY: dict[int, LLMStreamCancelHandle] = {}
_REGISTRY_LOCK = threading.Lock()
_CLEANUP_LOCKS: WeakValueDictionary[str, Any] = WeakValueDictionary()
# Stop may arrive after response headers but before the lazy generator starts.
# Keep its intent until an explicit Resume starts a fresh execution generation.
_CANCEL_REQUESTED: set[str] = set()
_FAILED_CANCEL_HANDLES: dict[str, LLMStreamCancelHandle] = {}
_FINISHED_RUNS: OrderedDict[str, None] = OrderedDict()
_FINISHED_RUN_KEEP = 1024
_PREPARED_RUNS: set[str] = set()

# Live direct-stream Workflow requests. Durable request metadata lives in the
# Workflow store; this in-memory rendezvous only wakes the currently running
# code-agent generator after the UI resolves that durable request.
_WORKFLOW_RESPONSES: dict[str, dict[str, Any] | None] = {}
_WORKFLOW_RESPONSE_LOCK = threading.Lock()


def submit_workflow_response(
    response_id: str,
    action: str,
    values: dict[str, Any] | None = None,
) -> bool:
    """Deliver a Workflow UI resolution to a live direct code-agent request."""
    with _WORKFLOW_RESPONSE_LOCK:
        if response_id not in _WORKFLOW_RESPONSES:
            return False
        _WORKFLOW_RESPONSES[response_id] = {
            "action": str(action or "accept"),
            "values": dict(values) if isinstance(values, dict) else {},
        }
    return True


def _cleanup_lock_for(run_id: str):
    with _REGISTRY_LOCK:
        lock = _CLEANUP_LOCKS.get(run_id)
        if lock is None:
            lock = threading.RLock()
            _CLEANUP_LOCKS[run_id] = lock
        return lock


def prepare_run(run_id: str, *, resume: bool = False) -> None:
    """Establish the next cancellation generation before exposing SSE headers."""
    with _cleanup_lock_for(run_id):
        with _REGISTRY_LOCK:
            if run_id in _CANCEL_REGISTRY or run_id in _PREPARED_RUNS:
                return
            if run_id in _FAILED_CANCEL_HANDLES:
                raise RuntimeError(f"run cancellation cleanup is incomplete: {run_id}")
            _FINISHED_RUNS.pop(run_id, None)
            if resume:
                from app.application.code_agent.tools._shell import clear_run_stop_marker

                clear_run_stop_marker(run_id)
                _CANCEL_REQUESTED.discard(run_id)
            _PREPARED_RUNS.add(run_id)


def request_cancel(run_id: str) -> bool:
    """Idempotently stop owned resources, including a not-yet-started stream."""
    with _REGISTRY_LOCK:
        ev = _CANCEL_REGISTRY.get(run_id)
        if ev is not None or run_id not in _FINISHED_RUNS:
            _CANCEL_REQUESTED.add(run_id)
        if ev is not None:
            ev.set()
    # Provider callbacks can be non-reentrant. All flags are signalled before
    # waiting here; a repeated request retries retained failed cleanup stages.
    with _cleanup_lock_for(run_id):
        return _cleanup_cancelled_run(run_id)


def _cleanup_cancelled_run(run_id: str) -> bool:
    """Flip the cancel event for `run_id`. Returns True if the run was
    known, False otherwise. Raises when owned live resources cannot be stopped,
    so callers never acknowledge an incomplete cancellation.

    Beyond setting the flag (read between steps), this also KILLS any live
    shell process the run launched. A blocking tool runs in a daemon worker
    thread that never reads the event until it returns, so killing the OS
    process is what makes Stop abort a hung command immediately instead of
    waiting out the shell timeout.
    """
    # Cancel inference first. Shell/server cleanup can involve OS process-tree
    # work and must not delay closing a hot llama.cpp connection.
    with _REGISTRY_LOCK:
        ev = _CANCEL_REGISTRY.get(run_id)
        upstream_handle = (
            _UPSTREAM_HANDLE_REGISTRY.get(id(ev)) if ev is not None
            else _FAILED_CANCEL_HANDLES.get(run_id)
        )
    if ev is not None:
        ev.set()
    cleanup_errors: list[Exception] = []
    if upstream_handle is not None:
        # Do not acknowledge /cancel until the provider's HTTP response is
        # closed. The UI may safely abort its SSE reader after this returns.
        try:
            upstream_handle.close()
        except Exception as exc:
            with _REGISTRY_LOCK:
                _FAILED_CANCEL_HANDLES[run_id] = upstream_handle
            cleanup_errors.append(exc)
        else:
            with _REGISTRY_LOCK:
                if _FAILED_CANCEL_HANDLES.get(run_id) is upstream_handle:
                    _FAILED_CANCEL_HANDLES.pop(run_id, None)

    # Attempt every cleanup stage even if an earlier one failed. The caller is
    # told about any surviving transport/process only after all owners had a
    # chance to stop.
    try:
        from app.application.code_agent.tools import kill_run_processes

        kill_run_processes(run_id)
    except Exception as exc:
        cleanup_errors.append(exc)
    try:
        from app.application.code_agent.tools import cancel_run_callbacks

        cancel_run_callbacks(run_id)
    except Exception as exc:
        cleanup_errors.append(exc)
    try:
        from app.application.code_agent.tools._run import (
            run_owned_servers,
            stop_run_servers,
        )

        stopped_servers = stop_run_servers(run_id)
        failed_remote_cleanup = [
            item
            for item in stopped_servers
            if item.get("remote_cleanup_status")
            not in {None, "stopped", "already_stopped"}
        ]
        remaining_servers = run_owned_servers(run_id)
        if failed_remote_cleanup or remaining_servers:
            raise RuntimeError(
                "background process cleanup incomplete"
                f" (remote_failures={len(failed_remote_cleanup)},"
                f" remaining={len(remaining_servers)})"
            )
    except Exception as exc:
        cleanup_errors.append(exc)
    if cleanup_errors:
        raise RuntimeError(
            f"live cleanup failed for run {run_id}: {cleanup_errors[0]}"
        ) from cleanup_errors[0]
    return ev is not None


def _register_run(run_id: str, *, resume: bool = False) -> threading.Event:
    with _cleanup_lock_for(run_id):
        return _claim_run(run_id, resume=resume)


def _claim_run(run_id: str, *, resume: bool) -> threading.Event:
    from app.core.release_runtime import begin_agent_run

    ev = threading.Event()
    with _REGISTRY_LOCK:
        if run_id in _CANCEL_REGISTRY:
            raise RuntimeError(f"run is already active: {run_id}")
        if run_id in _FAILED_CANCEL_HANDLES:
            raise RuntimeError(f"run cancellation cleanup is incomplete: {run_id}")
        _FINISHED_RUNS.pop(run_id, None)
        prepared = run_id in _PREPARED_RUNS
        _PREPARED_RUNS.discard(run_id)
        if (resume and not prepared) or run_id not in _CANCEL_REQUESTED:
            from app.application.code_agent.tools._shell import clear_run_stop_marker

            clear_run_stop_marker(run_id)
            _CANCEL_REQUESTED.discard(run_id)
        elif run_id in _CANCEL_REQUESTED:
            ev.set()
        begin_agent_run(run_id)
        _CANCEL_REGISTRY[run_id] = ev
        handle = LLMStreamCancelHandle()
        _UPSTREAM_HANDLE_REGISTRY[id(ev)] = handle
        if ev.is_set():
            handle.close()
    return ev


def _cancel_handle_for(cancel_event: threading.Event) -> LLMStreamCancelHandle:
    with _REGISTRY_LOCK:
        handle = _UPSTREAM_HANDLE_REGISTRY.get(id(cancel_event))
    if handle is None:
        raise RuntimeError("run cancellation handle is not registered")
    return handle


def _has_retained_cancel_handle(run_id: str) -> bool:
    """Delegation must retain its parent abort hook after a failed natural close."""
    with _REGISTRY_LOCK:
        return run_id in _FAILED_CANCEL_HANDLES


def _unregister_run(run_id: str) -> None:
    # A naturally finishing generator must not hide a transport whose close
    # failed while a concurrent Stop is deciding whether cleanup is confirmed.
    with _cleanup_lock_for(run_id):
        _release_run(run_id)


def _release_run(run_id: str) -> None:
    from app.core.release_runtime import end_agent_run

    with _REGISTRY_LOCK:
        ev = _CANCEL_REGISTRY.pop(run_id, None)
        if ev is not None:
            _CANCEL_REQUESTED.discard(run_id)
            _FINISHED_RUNS[run_id] = None
            while len(_FINISHED_RUNS) > _FINISHED_RUN_KEEP:
                _FINISHED_RUNS.popitem(last=False)
        upstream_handle = (
            _UPSTREAM_HANDLE_REGISTRY.pop(id(ev), None) if ev is not None else None
        )
        if ev is not None:
            end_agent_run(run_id)
    if upstream_handle is not None:
        try:
            upstream_handle.close()
        except Exception:
            with _REGISTRY_LOCK:
                _FAILED_CANCEL_HANDLES[run_id] = upstream_handle
            raise


@dataclass(frozen=True)
class WorkflowWaitResult:
    response: dict[str, Any] | None
    cancelled: bool


def begin_workflow_response(response_id: str) -> None:
    """Register the live rendezvous before publishing its Workflow event."""
    with _WORKFLOW_RESPONSE_LOCK:
        _WORKFLOW_RESPONSES[response_id] = None


def wait_workflow_response(
    *,
    response_id: str,
    cancel_event: threading.Event,
    step: int,
    response_first: bool = False,
) -> Generator[dict[str, Any], None, WorkflowWaitResult]:
    """Observe response and Stop with the caller's existing polling priority.

    Only ask_user uses response_first: an answer accepted just before Stop is
    retrieved again at pop and remains replayable. Other branches retain their
    cancellation-first behavior. No deadline is imposed on human input.
    """
    wait_started = time.monotonic()
    last_keepalive = wait_started
    response: dict[str, Any] | None = None
    while True:
        if not response_first and cancel_event.is_set():
            break
        with _WORKFLOW_RESPONSE_LOCK:
            stored = _WORKFLOW_RESPONSES.get(response_id)
        if stored is not None:
            response = dict(stored)
            break
        if response_first and cancel_event.is_set():
            break
        now = time.monotonic()
        if now - last_keepalive >= _WORKFLOW_REQUEST_KEEPALIVE_EVERY:
            yield {
                "type": "workflow_request_wait",
                "step": step,
                "response_id": response_id,
                "waited_s": int(now - wait_started),
            }
            last_keepalive = now
        time.sleep(_WORKFLOW_REQUEST_POLL_INTERVAL)
    with _WORKFLOW_RESPONSE_LOCK:
        stored = _WORKFLOW_RESPONSES.pop(response_id, None)
        if response_first and stored is not None:
            response = dict(stored)
    return WorkflowWaitResult(response=response, cancelled=cancel_event.is_set())
