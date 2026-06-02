"""Pydantic-схемы для Tool Registry (Agent OS Phase 2)."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class ToolDefinition(BaseModel):
    name: str = Field(..., min_length=1)
    display_name: str = Field("")
    display_name_ru: str = Field("")
    description: str = Field("")
    description_ru: str = Field("")
    category: str = Field("general")
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    source: str = Field("builtin")
    enabled: bool = True
    permission: str = Field("auto")
    side_effect: bool = False
    scopes: list[str] = Field(default_factory=list)
    timeout_seconds: int = 30
    max_output_chars: int = 50000
    idempotent: bool = False


class ToolUpdate(BaseModel):
    display_name: str | None = None
    display_name_ru: str | None = None
    description: str | None = None
    description_ru: str | None = None
    category: str | None = None
    parameters_schema: dict[str, Any] | None = None
    enabled: bool | None = None
    permission: str | None = None
    side_effect: bool | None = None
    scopes: list[str] | None = None
    timeout_seconds: int | None = None
    max_output_chars: int | None = None
    idempotent: bool | None = None


class ToolExecuteRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = Field(
        None,
        description="Stable run_id for approval retry. Server generates a UUID if absent.",
    )


class ToolExecuteResponse(BaseModel):
    ok: bool
    tool_name: str
    result: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    run_id: str = Field("", description="Always returned so client can retry after approval.")


class ToolListResponse(BaseModel):
    tools: list[dict[str, Any]]
    total: int
