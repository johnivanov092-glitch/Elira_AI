from __future__ import annotations

import logging
from typing import Any

from app.services.agent_monitor import record_agent_run_metric


logger = logging.getLogger(__name__)


def resolve_agent_os_source_id(
    agent_id: str | None,
    registry_agent: dict[str, Any] | None,
) -> str:
    return str(agent_id or (registry_agent or {}).get("id") or "")


def emit_agent_os_event(
    *,
    event_type: str,
    source_agent_id: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        from app.services.event_bus import emit_event

        emit_event(
            event_type=event_type,
            source_agent_id=source_agent_id,
            payload=payload or {},
        )
    except Exception:
        logger.debug("event_bus_emit_failed", exc_info=True)


def record_agent_os_monitoring(
    *,
    agent_id: str,
    run_id: str,
    route: str,
    model_name: str,
    ok: bool,
    duration_ms: int,
    streaming: bool,
    num_ctx: int,
    selected_tools: list[str] | None,
) -> None:
    try:
        record_agent_run_metric(
            agent_id=agent_id,
            run_id=run_id,
            route=route,
            model_name=model_name,
            ok=ok,
            duration_ms=duration_ms,
            streaming=streaming,
            num_ctx=int(num_ctx or 0),
            tools=list(selected_tools or []),
        )
    except Exception:
        logger.debug("agent_monitor_record_failed", exc_info=True)
