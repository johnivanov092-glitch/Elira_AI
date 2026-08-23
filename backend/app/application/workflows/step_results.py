from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

@dataclass(slots=True)
class WorkflowStepOutcome:
    save_key: str
    success: bool
    next_step_id: str | None


@dataclass(slots=True)
class WorkflowRequestSpec:
    kind: str
    message: str
    schema: dict[str, Any]
    sensitive: bool
    provider_ref: str = ""


def extract_workflow_request(
    step_result: dict[str, Any],
) -> WorkflowRequestSpec | None:
    raw = step_result.get("raw", {})
    if not isinstance(raw, dict):
        raw = {}
    request = step_result.get("request") or raw.get("request") or {}
    if not isinstance(request, dict):
        request = {}

    provider_ref = str(
        step_result.get("response_id")
        or raw.get("response_id")
        or ""
    ).strip()
    status = str(step_result.get("status") or raw.get("status") or "").strip()
    status_to_kind = {
        "needs_input": "input",
        "needs_secret": "secret",
        "needs_elevation": "elevation",
        "waiting_approval": "approval",
    }
    kind = str(request.get("kind") or status_to_kind.get(status, "")).strip()

    if kind not in {"input", "secret", "elevation", "approval"}:
        return None

    schema = request.get("schema", {})
    if not isinstance(schema, dict):
        schema = {}
    message = str(request.get("message", "")).strip()
    if kind == "secret" and not message:
        message = "Secret value required"
    return WorkflowRequestSpec(
        kind=kind,
        message=message,
        schema=schema,
        sensitive=kind == "secret" or bool(request.get("sensitive", False)),
        provider_ref=provider_ref,
    )


def build_step_result_from_exception(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
        "raw": {"ok": False, "error": str(exc)},
    }


def capture_step_outcome(
    step: dict[str, Any],
    *,
    current_step_id: str,
    step_result: dict[str, Any],
    step_results: dict[str, Any],
    resolve_next_step: Callable[[dict[str, Any]], str | None],
) -> WorkflowStepOutcome:
    save_key = str(step.get("save_as") or current_step_id)
    step_results[save_key] = step_result
    success = bool(step_result.get("ok"))
    next_step_id = resolve_next_step(step)
    return WorkflowStepOutcome(
        save_key=save_key,
        success=success,
        next_step_id=next_step_id,
    )


def build_step_completion_event(
    *,
    current_step_id: str,
    outcome: WorkflowStepOutcome,
    step_result: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    if outcome.success:
        return (
            "workflow.step.completed",
            {
                "step_id": current_step_id,
                "save_as": outcome.save_key,
                "next_step_id": outcome.next_step_id or None,
            },
        )
    return (
        "workflow.step.failed",
        {
            "step_id": current_step_id,
            "error": step_result.get("error", ""),
            "next_step_id": outcome.next_step_id or None,
        },
    )


def should_pause_after_step(step: dict[str, Any], step_result: dict[str, Any]) -> bool:
    return bool(step.get("pause_after")) or bool(step_result.get("pause_requested"))
