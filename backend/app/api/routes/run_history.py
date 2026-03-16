from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.agent_supervisor_service import AgentSupervisorService
from app.services.event_bus_service import EventBusService
from app.services.execution_runtime_service import ExecutionRuntimeService
from app.services.run_trace_service import RunTraceService
from app.services.supervisor_runtime_bridge_service import SupervisorRuntimeBridgeService


router = APIRouter(prefix="/api/run-history", tags=["run-history"])

event_bus = EventBusService()
execution_runtime = ExecutionRuntimeService(event_bus=event_bus)
supervisor = AgentSupervisorService(
    execution_runtime=execution_runtime,
    event_bus=event_bus,
)
run_trace_service = RunTraceService()
bridge = SupervisorRuntimeBridgeService(
    supervisor=supervisor,
    execution_runtime=execution_runtime,
    event_bus=event_bus,
    run_trace_service=run_trace_service,
)


class GoalRunRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    requested_by: str = "user"


@router.get("/status")
def status():
    return {
        "status": "ok",
        "storage_dir": str(run_trace_service.storage_dir),
        "runs_count": len(run_trace_service.list_runs(limit=1000)),
    }


@router.post("/run")
async def run_goal(payload: GoalRunRequest):
    return await bridge.run_goal_with_trace(
        goal=payload.goal,
        requested_by=payload.requested_by,
    )


@router.get("/runs")
def list_runs(limit: int = 50):
    return run_trace_service.list_runs(limit=limit)


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    run = run_trace_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.delete("/runs/{run_id}")
def delete_run(run_id: str):
    ok = run_trace_service.delete_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"status": "deleted", "run_id": run_id}
