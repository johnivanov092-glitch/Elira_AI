"""Ownership of live runs, inference cancellation and Workflow rendezvous.

Durable Workflow requests and session-level Stop remain with their existing
owners. This module owns only the active generator's registries and cleanup.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Generator

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


def request_cancel(run_id: str) -> bool:
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
            _UPSTREAM_HANDLE_REGISTRY.get(id(ev)) if ev is not None else None
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
            cleanup_errors.append(exc)

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


def _register_run(run_id: str) -> threading.Event:
    from app.core.release_runtime import begin_agent_run

    try:
        from app.application.code_agent.tools._shell import clear_run_stop_marker

        clear_run_stop_marker(run_id)
    except Exception:
        logger.warning("failed to clear stale Stop marker for run %s", run_id, exc_info=True)
    ev = threading.Event()
    with _REGISTRY_LOCK:
        if run_id in _CANCEL_REGISTRY:
            raise RuntimeError(f"run is already active: {run_id}")
        begin_agent_run(run_id)
        _CANCEL_REGISTRY[run_id] = ev
        _UPSTREAM_HANDLE_REGISTRY[id(ev)] = LLMStreamCancelHandle()
    return ev


def _cancel_handle_for(cancel_event: threading.Event) -> LLMStreamCancelHandle:
    with _REGISTRY_LOCK:
        handle = _UPSTREAM_HANDLE_REGISTRY.get(id(cancel_event))
    if handle is None:
        raise RuntimeError("run cancellation handle is not registered")
    return handle


def _unregister_run(run_id: str) -> None:
    from app.core.release_runtime import end_agent_run

    with _REGISTRY_LOCK:
        ev = _CANCEL_REGISTRY.pop(run_id, None)
        upstream_handle = (
            _UPSTREAM_HANDLE_REGISTRY.pop(id(ev), None) if ev is not None else None
        )
        if ev is not None:
            end_agent_run(run_id)
    if upstream_handle is not None:
        upstream_handle.close()


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
