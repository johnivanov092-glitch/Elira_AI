from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthComponent(BaseModel):
    component: str
    ok: bool
    detail: str = ""


class AgentHealth(BaseModel):
    agent_id: str
    ok: bool = True
    detail: str = ""


class SystemHealth(BaseModel):
    ok: bool
    components: list[HealthComponent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentLimit(BaseModel):
    agent_id: str
    max_runs_per_hour: int
    max_execution_seconds: int
    max_context_tokens: int
    allowed_tools: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class AgentLimitUpdate(BaseModel):
    max_runs_per_hour: int | None = None
    max_execution_seconds: int | None = None
    max_context_tokens: int | None = None
    allowed_tools: list[str] | None = None


class AgentLimitListResponse(BaseModel):
    items: list[AgentLimit] = Field(default_factory=list)
    total: int = 0


class AgentDashboardTopAgent(BaseModel):
    agent_id: str
    run_count: int


class AgentDashboardResponse(BaseModel):
    ok: bool
    window_hours: int
    total_agent_runs: int
    blocked_runs: int
    workflow_runs: int
    avg_duration_ms: int
    top_agents: list[AgentDashboardTopAgent] = Field(default_factory=list)
    recent_violations: list[dict[str, Any]] = Field(default_factory=list)
    limits_summary: list[AgentLimit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
