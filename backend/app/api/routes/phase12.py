from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.execution_history_service import ExecutionHistoryService
from app.services.execution_view_service import ExecutionViewService
from app.services.execution_runtime_bridge_service import ExecutionRuntimeBridgeService

try:
    from app.services.event_bus_service import EventBusService
except Exception:
    EventBusService = None

try:
    from app.services.autonomous_dev_engine_service import AutonomousDevEngineService
except Exception:
    AutonomousDevEngineService = None

try:
    from app.services.multi_agent_service import MultiAgentService
except Exception:
    MultiAgentService = None

try:
    from app.services.project_brain_engine_service import ProjectBrainEngineService
except Exception:
    ProjectBrainEngineService = None


router = APIRouter(prefix="/api/phase12", tags=["phase12"])

execution_history_service = ExecutionHistoryService()
execution_view_service = ExecutionViewService(execution_history_service)
event_bus = EventBusService() if EventBusService else None
autonomous_dev_engine = AutonomousDevEngineService() if AutonomousDevEngineService else None
multi_agent_service = MultiAgentService() if MultiAgentService else None
project_brain_engine = ProjectBrainEngineService() if ProjectBrainEngineService else None

bridge = ExecutionRuntimeBridgeService(
    execution_history_service=execution_history_service,
    event_bus=event_bus,
    autonomous_dev_engine=autonomous_dev_engine,
    multi_agent_service=multi_agent_service,
    project_brain_engine=project_brain_engine,
)


class StartExecutionRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    mode: str = "autonomous_dev"
    metadata: dict = Field(default_factory=dict)


@router.get("/status")
def status():
    return {
        "status": "ok",
        "phase": 12,
        "executions_count": len(execution_history_service.list_executions(limit=1000)),
        "modes": ["autonomous_dev", "multi_agent", "project_brain"],
    }


@router.get("/executions")
def list_executions(limit: int = 50):
    return execution_history_service.list_executions(limit=limit)


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str):
    view = execution_view_service.get_execution_view(execution_id)
    if not view:
        raise HTTPException(status_code=404, detail="Execution not found")
    return view


@router.get("/executions/{execution_id}/events")
def get_execution_events(execution_id: str):
    view = execution_view_service.get_execution_view(execution_id)
    if not view:
        raise HTTPException(status_code=404, detail="Execution not found")
    return {"execution_id": execution_id, "events": view["events"]}


@router.get("/executions/{execution_id}/artifacts")
def get_execution_artifacts(execution_id: str):
    view = execution_view_service.get_execution_view(execution_id)
    if not view:
        raise HTTPException(status_code=404, detail="Execution not found")
    return {"execution_id": execution_id, "artifacts": view["artifacts"]}


@router.post("/executions/start")
async def start_execution(payload: StartExecutionRequest):
    return await bridge.start_execution(
        goal=payload.goal,
        mode=payload.mode,
        metadata=payload.metadata,
    )
