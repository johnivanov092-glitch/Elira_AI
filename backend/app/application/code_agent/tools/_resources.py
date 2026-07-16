"""resource_process — the deferred read-only resource tool (R1).

One general tool over durable resources. It takes ONLY a resource_id + an
operation (never a path, never bytes). The resource_id is resolved against the
CURRENT run's binding — a resource that is unknown, owned by another session, or
not attached to this run fails closed before any bytes are read. There is no
loose global lookup by id.
"""
from __future__ import annotations

from typing import Any

_ERROR_TEXT = {
    "no_run_context": "resource_process requires a run context",
    "unknown_operation": "operation must be inspect, extract_text or transcribe",
    "resource_not_bound": "resource is not attached to this run",
    "resource_not_found": "resource not found",
}


def tool_resource_process(resource_id: str = "", operation: str = "", **_ignored: Any) -> dict[str, Any]:
    """Process a durable resource attached to this run. Args: resource_id (opaque
    id, NOT a path) + operation (inspect | extract_text | transcribe). Read-only;
    one bounded result; stable ``error`` code with ok=False on any refusal."""
    from app.application.code_agent.tools import get_current_run_id
    from app.application.media import processing, resource_store, run_binding

    run_id = get_current_run_id()
    resource_id = str(resource_id or "").strip()
    operation = str(operation or "").strip().lower()

    if operation not in processing.SUPPORTED_OPERATIONS:
        return {"ok": False, "error": "unknown_operation",
                "text": f"ERROR: {_ERROR_TEXT['unknown_operation']}"}
    if not run_id:
        return {"ok": False, "error": "no_run_context",
                "text": f"ERROR: {_ERROR_TEXT['no_run_context']}"}
    # Ownership/run gate FIRST — a bare id (or a path passed as an id) that is not
    # bound to this run is refused before any store lookup or byte read.
    if not run_binding.is_bound(run_id, resource_id):
        return {"ok": False, "error": "resource_not_bound",
                "text": f"ERROR: {_ERROR_TEXT['resource_not_bound']}"}
    record = resource_store.get_record(resource_id)
    if record is None:
        return {"ok": False, "error": "resource_not_found",
                "text": f"ERROR: {_ERROR_TEXT['resource_not_found']}"}
    return processing.process_resource(record, operation)
