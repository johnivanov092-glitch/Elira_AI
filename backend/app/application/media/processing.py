"""Explicit metadata inspection; content processing belongs to mutable skills."""
from __future__ import annotations

from typing import Any

from app.application.media import resource_store

INSPECT = "inspect"
SUPPORTED_OPERATIONS = (INSPECT,)
SUPPORTED_EXECUTION_TARGETS = ("auto", "local_cpu")

_LOCAL_CPU = "local_cpu"


def _err(operation: str, resource_id: str, code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "operation": operation, "resource_id": resource_id,
            "error": code, "text": f"ERROR: {message}"}




def _execution_result(result: dict[str, Any], *, requested_target: str,
                      selected_target: str | None, backend: str | None) -> dict[str, Any]:
    """Attach the public execution decision without exposing runtime details."""
    result["requested_target"] = requested_target
    result["selected_target"] = selected_target
    result["backend"] = backend
    result["execution_target"] = selected_target or requested_target
    if requested_target == "auto":
        result["fallback_chain"] = []
    return result


def _inspect(record: resource_store.ResourceRecord) -> dict[str, Any]:
    ref = resource_store.resource_ref(record)
    summary = (f"resource {ref['resource_id']}: name={ref['name']} kind={ref['kind']} "
               f"type={ref['content_type']} size={ref['size']} sha256={record.sha256}")
    return {"ok": True, "operation": INSPECT, "resource_id": record.resource_id,
            "kind": record.kind, "name": record.original_name,
            "content_type": record.content_type, "size": record.size,
            "sha256": record.sha256, "created_at": record.created_at,
            "execution_target": _LOCAL_CPU, "text": summary}




def process_resource(record: resource_store.ResourceRecord, operation: str,
                     execution_target: str = "auto") -> dict[str, Any]:
    """Dispatch one already-authorized local-CPU resource operation."""
    op = str(operation or "").strip().lower()
    if op not in SUPPORTED_OPERATIONS:
        return _err(op or "unknown", record.resource_id, "unknown_operation",
                    "unsupported resource operation")
    target = str(execution_target or "auto").strip().lower()
    if target not in SUPPORTED_EXECUTION_TARGETS:
        return _err(op, record.resource_id, "invalid_execution_target",
                    "execution_target must be auto or local_cpu")
    result = _inspect(record)
    return _execution_result(
        result, requested_target=target, selected_target=_LOCAL_CPU,
        backend="resource-inspect",
    )
