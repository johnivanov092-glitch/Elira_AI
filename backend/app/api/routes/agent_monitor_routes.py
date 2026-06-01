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


# ── Runs ─────────────────────────────────────────────────────────────────────

@router.get("/runs", summary="List recent tool execution runs")
def list_runs(
    agent_id: str | None = Query(None, description="Filter by agent_id from event payload"),
    source: str | None = Query(None, description="Filter by source (chat|code_agent|workflow)"),
    status: str | None = Query(None, description="Filter by status (ok|error|blocked|forbidden|waiting_approval)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Return recent tool.executed events, newest first.

    Each item contains tool_name, agent_id, source, run_id, status, ok, error
    fields from the event payload, plus the event's created_at timestamp.
    """
    from app.application.event_bus import runtime as event_bus

    events, total = event_bus.list_events(event_type="tool.executed", limit=limit, offset=offset)

    # Filter by payload fields if requested
    def _matches(evt: dict) -> bool:
        p = evt.get("payload", {})
        if agent_id and p.get("agent_id") != agent_id:
            return False
        if source and p.get("source") != source:
            return False
        if status and p.get("status") != status:
            return False
        return True

    runs = [
        {
            "event_id": e["event_id"],
            "created_at": e["created_at"],
            **{k: e["payload"].get(k) for k in
               ("tool_name", "agent_id", "source", "run_id",
                "project_scope_id", "workflow_id", "step_id",
                "status", "ok", "error")},
        }
        for e in events
        if _matches(e)
    ]
    return {"items": runs, "total": total}


# ── MemoryCandidate ───────────────────────────────────────────────────────────

@router.get("/memory/candidates", summary="List memory candidates")
def list_candidates(
    status: str | None = Query(None, description="pending|accepted|rejected|expired"),
    namespace: str | None = Query(None),
    project_scope_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    items = agent_monitor.list_candidates(
        status=status, namespace=namespace,
        project_scope_id=project_scope_id, limit=limit,
    )
    return {"items": items, "total": len(items)}


@router.get("/memory/candidates/{candidate_id}", summary="Get memory candidate")
def get_candidate(candidate_id: str):
    item = agent_monitor.get_candidate(candidate_id)
    if not item:
        raise HTTPException(404, f"Candidate '{candidate_id}' not found")
    return item


@router.post("/memory/candidates/{candidate_id}/accept", summary="Accept a memory candidate")
def accept_candidate(candidate_id: str, content: str | None = None):
    item = agent_monitor.get_candidate(candidate_id)
    if not item:
        raise HTTPException(404, f"Candidate '{candidate_id}' not found")
    if item["status"] not in ("pending", "rejected"):
        raise HTTPException(400, f"Cannot accept: status is '{item['status']}'")
    return agent_monitor.update_candidate_status(
        candidate_id, status="accepted", content=content
    )


@router.post("/memory/candidates/{candidate_id}/reject", summary="Reject a memory candidate")
def reject_candidate(candidate_id: str):
    item = agent_monitor.get_candidate(candidate_id)
    if not item:
        raise HTTPException(404, f"Candidate '{candidate_id}' not found")
    if item["status"] not in ("pending", "accepted"):
        raise HTTPException(400, f"Cannot reject: status is '{item['status']}'")
    return agent_monitor.update_candidate_status(candidate_id, status="rejected")


@router.delete("/memory/candidates/{candidate_id}", summary="Delete a memory candidate")
def delete_candidate(candidate_id: str):
    item = agent_monitor.get_candidate(candidate_id)
    if not item:
        raise HTTPException(404, f"Candidate '{candidate_id}' not found")
    return agent_monitor.delete_candidate(candidate_id)
