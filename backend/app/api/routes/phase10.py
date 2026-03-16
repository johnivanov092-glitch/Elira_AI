from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.research_pipeline_service import ResearchPipelineService
from app.services.browser_runtime_service import BrowserRuntimeService

try:
    from app.services.event_bus_service import EventBusService
except Exception:
    EventBusService = None

try:
    from app.services.run_trace_service import RunTraceService
except Exception:
    RunTraceService = None

try:
    from app.services.tool_service import ToolService
except Exception:
    ToolService = None

try:
    from app.services.browser_agent import BrowserAgent
except Exception:
    BrowserAgent = None


router = APIRouter(prefix="/api/phase10", tags=["phase10"])

event_bus = EventBusService() if EventBusService else None
run_trace_service = RunTraceService() if RunTraceService else None
tool_service = ToolService() if ToolService else None
browser_agent = BrowserAgent() if BrowserAgent else None

research_service = ResearchPipelineService(
    tool_service=tool_service,
    run_trace_service=run_trace_service,
    event_bus=event_bus,
)

browser_runtime_service = BrowserRuntimeService(
    browser_agent=browser_agent,
    run_trace_service=run_trace_service,
    event_bus=event_bus,
)


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    multi_search: bool = True
    dedupe: bool = True
    documentation_mode: bool = False
    max_results: int = 12


class BrowserRequest(BaseModel):
    url: str = Field(..., min_length=1)
    actions: list[dict] = Field(default_factory=list)


@router.get("/status")
def status():
    return {
        "status": "ok",
        "phase": 10,
        "services": {
            "research_pipeline": True,
            "browser_runtime": True,
            "tool_service": tool_service is not None,
            "browser_agent": browser_agent is not None,
        },
    }


@router.post("/research/run")
async def run_research(payload: ResearchRequest):
    return await research_service.run(
        query=payload.query,
        multi_search=payload.multi_search,
        dedupe=payload.dedupe,
        documentation_mode=payload.documentation_mode,
        max_results=payload.max_results,
    )


@router.post("/browser/run")
async def run_browser(payload: BrowserRequest):
    return await browser_runtime_service.execute(
        url=payload.url,
        actions=payload.actions,
    )
