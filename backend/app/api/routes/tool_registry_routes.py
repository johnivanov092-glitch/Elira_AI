"""REST API для Tool Registry (Agent OS Phase 2).

Префикс: /api/agent-os/tools
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.tool_registry import (
    ToolDefinition,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolListResponse,
    ToolUpdate,
)
import app.application.tools.tool_registry as registry

router = APIRouter(prefix="/api/agent-os", tags=["agent-os"])


@router.get("/tools", summary="Список инструментов", response_model=ToolListResponse)
def list_tools(
    category: str | None = Query(None),
    source: str | None = Query(None),
    include_disabled: bool = Query(False),
):
    tools = registry.list_tools_with_schemas(
        category=category,
        source=source,
        enabled_only=not include_disabled,
    )
    return ToolListResponse(tools=tools, total=len(tools))


@router.get("/tools/{name}", summary="Детали инструмента")
def get_tool(name: str):
    tool = registry.get_tool(name)
    if not tool:
        raise HTTPException(404, f"Tool '{name}' not found")
    return tool


@router.post("/tools", summary="Зарегистрировать custom tool")
def register_tool(body: ToolDefinition):
    result = registry.register_tool_from_dict(body.model_dump())
    return result


@router.patch("/tools/{name}", summary="Обновить метаданные инструмента")
def update_tool(name: str, body: ToolUpdate):
    existing = registry.get_tool(name)
    if not existing:
        raise HTTPException(404, f"Tool '{name}' not found")
    return registry.update_tool(name, body.model_dump(exclude_none=True))


@router.delete("/tools/{name}", summary="Удалить инструмент")
def delete_tool(name: str):
    existing = registry.get_tool(name)
    if not existing:
        raise HTTPException(404, f"Tool '{name}' not found")
    return registry.delete_tool(name)


@router.post("/tools/{name}/execute", summary="Выполнить инструмент", response_model=ToolExecuteResponse)
def execute_tool(name: str, body: ToolExecuteRequest):
    tool = registry.get_tool(name)
    if not tool:
        raise HTTPException(404, f"Tool '{name}' not found")

    try:
        result = registry.execute_tool(name, body.args)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {exc}") from exc
    ok = result.get("ok", False) if isinstance(result, dict) else False
    errors = [result.get("error", "")] if not ok and isinstance(result, dict) and result.get("error") else []

    return ToolExecuteResponse(
        ok=ok,
        tool_name=name,
        result=result if isinstance(result, dict) else {"value": result},
        errors=errors,
    )


@router.post("/tools/{name}/validate", summary="Валидировать аргументы")
def validate_tool(name: str, body: ToolExecuteRequest):
    errors = registry.validate_tool_args(name, body.args)
    return {"ok": len(errors) == 0, "tool_name": name, "errors": errors}
