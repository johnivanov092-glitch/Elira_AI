"""Pydantic-схемы для Agent Registry (Agent OS Phase 1)."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class AgentDefinition(BaseModel):
    id: str = Field("", description="Slug-ID агента (auto-generated если пустой)")
    name: str = Field(..., min_length=1, description="English display name")
    name_ru: str = Field("", description="Русское имя")
    description: str = Field("", description="English description")
    description_ru: str = Field("", description="Описание на русском")
    role: str = Field("general", description="researcher | programmer | analyst | orchestrator | custom")
    system_prompt: str = Field("", description="System prompt для агента")
    model_preference: str = Field("", description="Предпочтительная модель (пустая = auto)")
    capabilities: list[str] = Field(default_factory=list, description="Список доступных tool names")
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict, description="Произвольная конфигурация агента")
    enabled: bool = True
    version: int = 1


class AgentUpdate(BaseModel):
    name: str | None = None
    name_ru: str | None = None
    description: str | None = None
    description_ru: str | None = None
    role: str | None = None
    system_prompt: str | None = None
    model_preference: str | None = None
    capabilities: list[str] | None = None
    tags: list[str] | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class AgentStateData(BaseModel):
    state: dict[str, Any] = Field(default_factory=dict)


class AgentRunRecord(BaseModel):
    agent_id: str
    run_id: str
    input_summary: str = ""
    output_summary: str = ""
    ok: bool = False
    route: str = ""
    model_used: str = ""
    duration_ms: int = 0


class AgentListResponse(BaseModel):
    agents: list[dict[str, Any]]
    total: int


class AgentRunsResponse(BaseModel):
    runs: list[dict[str, Any]]
    total: int
