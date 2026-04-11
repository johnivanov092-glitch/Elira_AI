from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.agent_monitor import (
    WORKFLOW_ENGINE_AGENT_ID,
    record_resource_usage,
    record_workflow_run_metric,
    record_workflow_step_metric,
)


@dataclass(slots=True)
class WorkflowExecutionState:
    graph: dict[str, Any]
    steps_by_id: dict[str, dict[str, Any]]
    ordered_ids: list[str]
    total_steps: int
    workflow_input: dict[str, Any]
    context: dict[str, Any]
    step_results: dict[str, Any]
    current_step_id: str
    run_state: dict[str, Any]


def parse_workflow_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def workflow_duration_ms(run: dict[str, Any]) -> int:
    started_at = parse_workflow_datetime(str(run.get("started_at", "")))
    if not started_at:
        return 0
    return max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))


def record_workflow_run_state(
    run: dict[str, Any],
    *,
    status: str,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        record_workflow_run_metric(
            workflow_id=str(run.get("workflow_id", "")),
            run_id=str(run.get("run_id", "")),
            status=status,
            duration_ms=workflow_duration_ms(run),
            details=details or {},
        )
    except Exception:
        return


def build_workflow_execution_state(
    *,
    run: dict[str, Any],
    template: dict[str, Any],
    get_workflow_run: Callable[[str], dict[str, Any] | None],
) -> WorkflowExecutionState:
    graph = template.get("graph", {})
    steps = graph.get("steps", []) if isinstance(graph, dict) else []
    steps_by_id = {str(step.get("id")): step for step in steps}
    ordered_ids = [str(step.get("id")) for step in steps]
    run_id = str(run.get("run_id", ""))
    return WorkflowExecutionState(
        graph=graph if isinstance(graph, dict) else {},
        steps_by_id=steps_by_id,
        ordered_ids=ordered_ids,
        total_steps=len(ordered_ids),
        workflow_input=run.get("input", {}),
        context=run.get("context", {}),
        step_results=run.get("step_results", {}),
        current_step_id=str(run.get("current_step_id", "")).strip(),
        run_state=get_workflow_run(run_id) or run,
    )


def workflow_step_index(
    current_step_id: str,
    ordered_ids: list[str],
    step_results: dict[str, Any],
) -> int:
    if current_step_id in ordered_ids:
        return ordered_ids.index(current_step_id) + 1
    return len(step_results) + 1


def record_workflow_total_steps(
    run: dict[str, Any],
    *,
    run_id: str,
    total_steps: int,
) -> None:
    try:
        record_resource_usage(
            agent_id=WORKFLOW_ENGINE_AGENT_ID,
            run_id=run_id,
            workflow_id=str(run.get("workflow_id", "")),
            resource="workflow_total_steps",
            amount=total_steps,
            unit="count",
            details={"trigger_source": run.get("trigger_source", "api")},
        )
    except Exception:
        return


def record_workflow_step_state(
    step: dict[str, Any],
    *,
    workflow_id: str,
    run_id: str,
    step_id: str,
    step_result: dict[str, Any],
    step_index: int,
    step_duration_ms: int,
    next_step_id: str | None,
    step_label: str,
) -> None:
    success = bool(step_result.get("ok"))
    step_agent_id = str(step.get("agent_id", "")).strip() if step.get("type") == "agent" else WORKFLOW_ENGINE_AGENT_ID
    step_details = {
        "label": step_label,
        "index": step_index,
        "next_step_id": next_step_id or None,
    }
    if step.get("type") == "tool":
        step_details["tool_name"] = str(step.get("tool_name", "")).strip()
    if step_result.get("error"):
        step_details["error"] = str(step_result.get("error", ""))[:500]
    if step_result.get("sandbox_reason"):
        step_details["sandbox_reason"] = step_result.get("sandbox_reason")

    try:
        record_workflow_step_metric(
            agent_id=step_agent_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            step_type=str(step.get("type", "")),
            ok=success,
            duration_ms=step_duration_ms,
            details=step_details,
        )
        record_resource_usage(
            agent_id=step_agent_id,
            run_id=run_id,
            workflow_id=workflow_id,
            step_id=step_id,
            resource="step_duration",
            amount=step_duration_ms,
            unit="ms",
            details={"step_type": str(step.get("type", "")), "label": step_label},
        )
    except Exception:
        return
