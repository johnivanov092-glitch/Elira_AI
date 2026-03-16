from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.multi_agent_service import MultiAgentService

try:
    from app.services.event_bus_service import EventBusService
except Exception:
    EventBusService = None

try:
    from app.services.run_trace_service import RunTraceService
except Exception:
    RunTraceService = None

try:
    from app.services.project_brain_engine_service import ProjectBrainEngineService
except Exception:
    ProjectBrainEngineService = None

try:
    from app.services.autonomous_dev_engine_service import AutonomousDevEngineService
except Exception:
    AutonomousDevEngineService = None

try:
    from app.services.tool_service import ToolService
except Exception:
    ToolService = None


router = APIRouter(prefix="/api/multi-agent", tags=["multi-agent"])

event_bus = EventBusService() if EventBusService else None
run_trace_service = RunTraceService() if RunTraceService else None
project_brain_service = ProjectBrainEngineService() if ProjectBrainEngineService else None
autonomous_dev_engine = AutonomousDevEngineService() if AutonomousDevEngineService else None
tool_service = ToolService() if ToolService else None

service = MultiAgentService(
    event_bus=event_bus,
    run_trace_service=run_trace_service,
    project_brain_service=project_brain_service,
    autonomous_dev_engine=autonomous_dev_engine,
    tool_service=tool_service,
)


class MultiAgentRunRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    auto_apply: bool = False
    run_checks: bool = True


@router.get("/status")
def status():
    agents = service.bootstrap_default_agents()
    return {
        "status": "ok",
        "agents_count": len(agents),
        "runs_count": len(service.list_runs(limit=1000)),
    }


@router.get("/agents")
def agents():
    return service.bootstrap_default_agents()


@router.post("/bootstrap")
def bootstrap():
    return service.bootstrap_default_agents()


@router.post("/run")
async def run_pipeline(payload: MultiAgentRunRequest):
    return await service.run_pipeline(
        goal=payload.goal,
        auto_apply=payload.auto_apply,
        run_checks=payload.run_checks,
    )


@router.get("/runs")
def runs(limit: int = 20):
    return service.list_runs(limit=limit)
