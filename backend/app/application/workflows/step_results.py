from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.agent_sandbox import SandboxPolicyError


@dataclass(slots=True)
class WorkflowStepOutcome:
    save_key: str
    success: bool
    next_step_id: str | None


def build_step_result_from_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, SandboxPolicyError):
        return {
            "ok": False,
            "error": str(exc),
            "sandbox_reason": exc.reason,
            "sandbox_details": exc.details,
            "raw": {"ok": False, "error": str(exc)},
        }
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
