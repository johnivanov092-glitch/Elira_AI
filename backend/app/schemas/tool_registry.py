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


class ToolUpdate(BaseModel):
    display_name: str | None = None
    display_name_ru: str | None = None
    description: str | None = None
    description_ru: str | None = None
    category: str | None = None
    parameters_schema: dict[str, Any] | None = None
    enabled: bool | None = None


class ToolExecuteRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class ToolExecuteResponse(BaseModel):
    ok: bool
    tool_name: str
    result: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class ToolListResponse(BaseModel):
    tools: list[dict[str, Any]]
    total: int
