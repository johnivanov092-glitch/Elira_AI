"""Workflow event emission helpers."""
from __future__ import annotations

from typing import Any


def emit_workflow_event(
    event_type: str,
    workflow_id: str,
    run_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        from app.services.event_bus import emit_event

        emit_event(
            event_type=event_type,
            source_agent_id=workflow_id,
            payload={"workflow_id": workflow_id, "run_id": run_id, **(payload or {})},
        )
    except Exception:
        return
