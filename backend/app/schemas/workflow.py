from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


WorkflowStepType = Literal["agent", "tool"]
WorkflowTransitionWhen = Literal["always", "on_success", "on_failure"]


class WorkflowTransition(BaseModel):
    to: str = Field(..., min_length=1)
    when: WorkflowTransitionWhen = "always"


class WorkflowStep(BaseModel):
    id: str = Field(..., min_length=1)
    type: WorkflowStepType
    agent_id: str = ""
    tool_name: str = ""
    input_map: dict[str, Any] = Field(default_factory=dict)
    save_as: str = ""
    next: str | list[WorkflowTransition] | None = None
    on_error: str = ""
    pause_after: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowGraph(BaseModel):
    entry_step: str = Field(..., min_length=1)
    steps: list[WorkflowStep] = Field(default_factory=list)


class WorkflowTemplateCreate(BaseModel):
    id: str = ""
    name: str = Field(..., min_length=1)
    name_ru: str = ""
    description: str = ""
    description_ru: str = ""
    graph: WorkflowGraph
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    version: int = 1
    source: str = "custom"


class WorkflowTemplateUpdate(BaseModel):
    name: str | None = None
    name_ru: str | None = None
    description: str | None = None
    description_ru: str | None = None
    graph: WorkflowGraph | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    enabled: bool | None = None
    version: int | None = None
    source: str | None = None


class WorkflowTemplate(BaseModel):
    id: str
    name: str
    name_ru: str = ""
    description: str = ""
    description_ru: str = ""
    graph: WorkflowGraph
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    version: int = 1
    source: str = "custom"
    created_at: str
    updated_at: str


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowTemplate]
    total: int


class WorkflowRunCreate(BaseModel):
    workflow_id: str = Field(..., min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    trigger_source: str = "api"


class WorkflowResumeRequest(BaseModel):
    context_patch: dict[str, Any] = Field(default_factory=dict)


class WorkflowRun(BaseModel):
    run_id: str
    workflow_id: str
    status: str
    current_step_id: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    step_results: dict[str, Any] = Field(default_factory=dict)
    pending_steps: list[str] = Field(default_factory=list)
    error: dict[str, Any] = Field(default_factory=dict)
    requested_pause: bool = False
    started_at: str
    updated_at: str
    finished_at: str | None = None
    trigger_source: str = "api"


class WorkflowRunListResponse(BaseModel):
    runs: list[WorkflowRun]
    total: int
