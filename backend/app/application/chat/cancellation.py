"""Cooperative cancellation registry for regular-chat (planner) runs.

The code-agent has its own ``_CANCEL_REGISTRY`` in ``code_agent/agent_loop.py``;
this is the equivalent for the PlannerV2 ``/api/chat/stream`` route so a Stop
button can actually halt live generation instead of leaving the local server
busy after the client disconnects.

Each streaming run registers a ``threading.Event`` under its ``run_id``. The
LLM streaming loop checks the event between tokens and closes the upstream
request when it is set; the ``/api/chat/cancel`` route sets it.
"""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_CANCEL_REGISTRY: dict[str, threading.Event] = {}


def register_run(run_id: str) -> threading.Event:
    """Create (or reuse) the cancel event for ``run_id`` and return it."""
    event = threading.Event()
    if run_id:
        with _LOCK:
            _CANCEL_REGISTRY[run_id] = event
    return event


def unregister_run(run_id: str) -> None:
    """Drop the cancel event for ``run_id`` once the run is finished."""
    if not run_id:
        return
    with _LOCK:
        _CANCEL_REGISTRY.pop(run_id, None)


def request_cancel(run_id: str) -> bool:
    """Signal cancellation for ``run_id``. Returns True if a run was found."""
    if not run_id:
        return False
    with _LOCK:
        event = _CANCEL_REGISTRY.get(run_id)
    if event is None:
        return False
    event.set()
    return True
