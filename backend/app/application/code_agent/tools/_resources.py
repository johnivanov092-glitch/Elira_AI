"""resource_process — the deferred read-only resource tool (R1 + R2).

One general tool over durable resources. It takes ONLY a resource_id, an
operation, and an execution_target enum — never a path, URL, hostname, argv,
command, model path, or env. The resource_id is resolved against the CURRENT
run's binding — a resource that is unknown, owned by another session, or not
attached to this run fails closed before any bytes are read. The runtime resolves
the id, checks the operation, reads the capability catalog, selects a registered
adapter, hands the adapter the internal path, and returns a bounded result. The
file extension is NOT a routing input; it only lets an adapter validate the
container after the user has stated the task.
"""
from __future__ import annotations

from typing import Any

_ERROR_TEXT = {
    "no_run_context": "resource_process requires a run context",
    "unknown_operation": "operation must be inspect, extract_text or transcribe",
    "invalid_execution_target": "execution_target must be auto, local_gpu, local_cpu or server_gpu",
    "unsupported_arguments": "resource_process accepts only resource_id, operation and execution_target",
    "resource_not_bound": "resource is not attached to this run",
    "resource_not_found": "resource not found",
}


def _refusal(code: str, *, requested_target: str | None) -> dict[str, Any]:
    """Return one stable execution-shaped refusal without echoing invalid input."""
    out: dict[str, Any] = {
        "ok": False,
        "error": code,
        "requested_target": requested_target,
        "selected_target": None,
        "backend": None,
        "text": f"ERROR: {_ERROR_TEXT[code]}",
    }
    if requested_target == "auto":
        out["fallback_chain"] = []
    return out


def tool_resource_process(resource_id: str = "", operation: str = "",
                          execution_target: str = "auto", **extra: Any) -> dict[str, Any]:
    """Process a durable resource attached to this run. Args: resource_id (opaque
    id, NOT a path), operation (inspect | extract_text | transcribe), and
    execution_target (auto | local_gpu | local_cpu | server_gpu). Read-only; one
    bounded result; stable ``error`` code with ok=False on any refusal."""
    from app.application.code_agent.tools import get_current_run_id
    from app.application.media import execution, processing, resource_store, run_binding

    run_id = get_current_run_id()
    resource_id = str(resource_id or "").strip()
    operation = str(operation or "").strip().lower()
    target = str(execution_target or "auto").strip().lower()

    if operation not in processing.SUPPORTED_OPERATIONS:
        safe_target = target if execution.valid_execution_target(target) else None
        return _refusal("unknown_operation", requested_target=safe_target)
    if not execution.valid_execution_target(target):
        return _refusal("invalid_execution_target", requested_target=None)
    # Do not silently accept path/url/argv/backend-like model arguments. The
    # runtime owns every execution detail; extra keys fail closed without echoing
    # their names or values.
    if extra:
        return _refusal("unsupported_arguments", requested_target=target)
    if not run_id:
        return _refusal("no_run_context", requested_target=target)
    # Ownership/run gate FIRST — a bare id (or a path passed as an id) that is not
    # bound to this run is refused before any store lookup or byte read.
    if not run_binding.is_bound(run_id, resource_id):
        return _refusal("resource_not_bound", requested_target=target)
    record = resource_store.get_record(resource_id)
    if record is None:
        return _refusal("resource_not_found", requested_target=target)
    return processing.process_resource(record, operation, target)
