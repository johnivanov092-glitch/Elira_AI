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


# ── Approvals ────────────────────────────────────────────────────────────────

@router.get("/approvals", summary="List tool-call approvals")
def list_approvals(
    status: str | None = Query(None, description="Filter by status: pending|approved|rejected|expired|used"),
    agent_id: str | None = Query(None),
    tool_name: str | None = Query(None),
    run_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    agent_monitor.expire_old_approvals()
    items = agent_monitor.list_approvals(
        status=status, agent_id=agent_id, tool_name=tool_name, run_id=run_id, limit=limit
    )
    return {"items": items, "total": len(items)}


@router.get("/approvals/{approval_id}", summary="Get approval details")
def get_approval(approval_id: str):
    item = agent_monitor.get_approval(approval_id)
    if not item:
        raise HTTPException(404, f"Approval '{approval_id}' not found")
    return item


@router.post("/approvals/{approval_id}/approve", summary="Approve a pending tool call")
def approve_approval(approval_id: str):
    item = agent_monitor.get_approval(approval_id)
    if not item:
        raise HTTPException(404, f"Approval '{approval_id}' not found")
    if item["status"] != "pending":
        raise HTTPException(400, f"Cannot approve: status is '{item['status']}' (must be 'pending')")
    return agent_monitor.update_approval_status(approval_id, status="approved")


@router.post("/approvals/{approval_id}/reject", summary="Reject a pending tool call")
def reject_approval(approval_id: str):
    item = agent_monitor.get_approval(approval_id)
    if not item:
        raise HTTPException(404, f"Approval '{approval_id}' not found")
    if item["status"] != "pending":
        raise HTTPException(400, f"Cannot reject: status is '{item['status']}' (must be 'pending')")
    return agent_monitor.update_approval_status(approval_id, status="rejected")
