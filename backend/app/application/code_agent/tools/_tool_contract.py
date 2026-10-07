"""Structured result contract shared by the integration tools (mcp, telegram, itops_registry)."""
from __future__ import annotations

import json
from typing import Any


RESULT_STATUSES = {
    "completed",
    "failed",
    "needs_input",
    "needs_secret",
    "needs_elevation",
    "waiting_approval",
    "cancelled",
}


class RuntimeRequest(Exception):
    def __init__(self, status: str, request: dict[str, Any]):
        super().__init__(str(request.get("message") or status))
        self.status = status
        self.request = request


def with_text(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
    }


def completed(operation: str, result: dict[str, Any]) -> dict[str, Any]:
    return with_text({
        "ok": True,
        "status": "completed",
        "operation": operation,
        "result": result,
    })


def failed(
    operation: str,
    error: str,
    *,
    code: str = "runtime_operation_failed",
    retryable: bool = False,
) -> dict[str, Any]:
    return with_text({
        "ok": False,
        "status": "failed",
        "operation": operation,
        "error": {
            "code": code,
            "message": str(error),
            "retryable": bool(retryable),
        },
    })


def requested(operation: str, exc: RuntimeRequest) -> dict[str, Any]:
    return with_text({
        "ok": False,
        "status": exc.status,
        "operation": operation,
        "request": exc.request,
    })


def input_request(message: str, field: str, title: str) -> RuntimeRequest:
    return RuntimeRequest(
        "needs_input",
        {
            "kind": "input",
            "message": message,
            "schema": {
                "type": "object",
                "properties": {
                    field: {"type": "string", "title": title},
                },
                "required": [field],
                "additionalProperties": False,
            },
            "sensitive": False,
        },
    )


def secret_request(
    message: str,
    *,
    kind: str = "token",
    existing_secret_ref: str = "",
    asset_id: str = "",
) -> RuntimeRequest:
    schema: dict[str, Any] = {"x-elira-secret-kind": kind}
    if existing_secret_ref:
        schema["x-elira-existing-secret-ref"] = existing_secret_ref
    if asset_id:
        schema["x-elira-asset-id"] = asset_id
    return RuntimeRequest(
        "needs_secret",
        {
            "kind": "secret",
            "message": message,
            "schema": schema,
            "sensitive": True,
        },
    )


def require_id(value: str, name: str, title: str | None = None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        # Missing tool arguments are model-correctable errors. A genuine need
        # for user input is requested explicitly through the Workflow plane.
        raise ValueError(f"Missing required argument: {name} ({title or name})")
    return normalized


def run_operation(operation: str, call: Any) -> dict[str, Any]:
    """Run one integration operation and wrap its outcome in the shared contract.

    ``call`` returns the runtime's own result dict or raises; a RuntimeRequest
    becomes a needs_* status that the agent loop turns into a Workflow card.
    """
    from app.application.agent_kernel.tool_result import ensure_tool_result

    try:
        result = ensure_tool_result(call(), source=f"operation {operation!r}")
    except RuntimeRequest as exc:
        return requested(operation, exc)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model as a typed failure
        return failed(
            operation,
            str(exc),
            code=exc.__class__.__name__,
            retryable=isinstance(exc, (ConnectionError, OSError))
            and not isinstance(exc, (FileNotFoundError, PermissionError)),
        )
    raw_status = str(result.get("status") or "").strip()
    if raw_status == "cancelled":
        return with_text({"ok": False, "status": "cancelled", "operation": operation, "result": result})
    if raw_status in RESULT_STATUSES - {"completed", "failed", "cancelled"}:
        request = result.get("request") if isinstance(result.get("request"), dict) else {}
        return with_text({"ok": False, "status": raw_status, "operation": operation,
                          "request": request, "result": result})
    if raw_status == "failed" or result.get("ok") is False:
        raw_error = result.get("error") or "operation failed"
        if isinstance(raw_error, dict):
            return failed(operation, str(raw_error.get("message") or raw_error),
                          code=str(raw_error.get("code") or "runtime_operation_failed"),
                          retryable=bool(raw_error.get("retryable", False)))
        return failed(operation, str(raw_error))
    return completed(operation, result)
