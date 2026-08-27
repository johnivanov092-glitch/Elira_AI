"""Structured result contract shared by runtime_control domain adapters."""
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
        raise input_request(
            f"Укажите {title or name} для продолжения.",
            name,
            title or name,
        )
    return normalized
