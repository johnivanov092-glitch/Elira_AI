from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.agent_supervisor_service import AgentSupervisorService
from app.services.event_bus_service import EventBusService
from app.services.execution_runtime_service import ExecutionRuntimeService
from app.services.task_scheduler_service import TaskSchedulerService


router = APIRouter(prefix="/api/supervisor", tags=["supervisor"])

event_bus = EventBusService()
execution_runtime = ExecutionRuntimeService(event_bus=event_bus)
scheduler = TaskSchedulerService()
supervisor = AgentSupervisorService(
    execution_runtime=execution_runtime,
    event_bus=event_bus,
)


class RegisterAgentRequest(BaseModel):
    name: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    allowed_tools: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class GoalRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    requested_by: str = "user"


class ScheduleGoalRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    delay_seconds: float = 5.0


@router.get("/status")
def supervisor_status():
    return {
        "status": "ok",
        "agents_count": len(supervisor.list_agents()),
        "runs_count": len(execution_runtime.list_runs(limit=1000)),
        "jobs_count": len(scheduler.list_jobs()),
    }


@router.get("/agents")
def list_agents():
    return supervisor.list_agents()


@router.post("/agents/register")
async def register_agent(payload: RegisterAgentRequest):
    return supervisor.register_agent(
        name=payload.name,
        role=payload.role,
        allowed_tools=payload.allowed_tools,
        metadata=payload.metadata,
    )


@router.post("/run")
async def run_goal(payload: GoalRequest):
    return await supervisor.run_goal(
        goal=payload.goal,
        requested_by=payload.requested_by,
    )


@router.get("/runs")
def list_runs(limit: int = 20):
    return execution_runtime.list_runs(limit=limit)


@router.get("/events")
def list_events(limit: int = 50):
    return event_bus.list_events(limit=limit)


@router.post("/schedule")
async def schedule_goal(payload: ScheduleGoalRequest):
    job = scheduler.create_once(
        title=f"Run goal: {payload.goal}",
        delay_seconds=payload.delay_seconds,
        callback=supervisor.run_goal,
        goal=payload.goal,
        requested_by="scheduler",
    )
    return job


@router.get("/jobs")
def list_jobs():
    return scheduler.list_jobs()


@router.post("/bootstrap")
async def bootstrap_default_agents():
    created = [
        supervisor.register_agent("Planner", "planner", ["research_web", "search_project"]),
        supervisor.register_agent("Research", "researcher", ["research_web", "search_web"]),
        supervisor.register_agent("Coder", "coder", ["read_project_file", "write_project_file"]),
        supervisor.register_agent("Reviewer", "reviewer", ["read_project_file"]),
    ]
    await event_bus.publish("supervisor.bootstrap", {"count": len(created)})
    return created
