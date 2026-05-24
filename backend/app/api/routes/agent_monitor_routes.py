from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.agent_monitor import (
    AgentDashboardResponse,
    AgentLimit,
    AgentLimitListResponse,
    AgentLimitUpdate,
    SystemHealth,
)
from app.application.monitoring import runtime as agent_monitor


router = APIRouter(prefix="/api/agent-os", tags=["agent-os"])


@router.get("/health", response_model=SystemHealth, summary="Agent OS component health")
def get_agent_os_health():
    return agent_monitor.get_agent_os_health()


@router.get("/dashboard", response_model=AgentDashboardResponse, summary="Agent OS dashboard aggregates")
def get_agent_os_dashboard(window_hours: int = Query(24, ge=1, le=168)):
    return agent_monitor.get_agent_os_dashboard(window_hours=window_hours)


@router.get("/limits", response_model=AgentLimitListResponse, summary="List sandbox limits")
def list_agent_limits():
    items = agent_monitor.list_agent_limits()
    return AgentLimitListResponse(items=items, total=len(items))


@router.get("/limits/{agent_id}", response_model=AgentLimit, summary="Get sandbox limit for an agent")
def get_agent_limit(agent_id: str):
    item = agent_monitor.ensure_agent_limit(agent_id)
    if not item:
        raise HTTPException(404, f"Agent limit '{agent_id}' not found")
    return item


@router.put("/limits/{agent_id}", response_model=AgentLimit, summary="Update sandbox limit for an agent")
def put_agent_limit(agent_id: str, body: AgentLimitUpdate):
    try:
        return agent_monitor.update_agent_limit(agent_id, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
