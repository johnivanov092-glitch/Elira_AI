"""REST API для Agent Registry (Agent OS Phase 1).

Префикс: /api/agent-os/agents
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.agent_registry import (
    AgentDefinition,
    AgentListResponse,
    AgentRunRecord,
    AgentRunsResponse,
    AgentStateData,
    AgentUpdate,
)
from app.services import agent_registry as registry

router = APIRouter(prefix="/api/agent-os", tags=["agent-os"])


# ── Агенты CRUD ──────────────────────────────────────────────

@router.post("/agents", summary="Зарегистрировать агента")
def create_agent(body: AgentDefinition):
    result = registry.register_agent(body.model_dump())
    if not result:
        raise HTTPException(400, "Не удалось зарегистрировать агента")
    return result


@router.get("/agents", summary="Список агентов", response_model=AgentListResponse)
def list_agents(
    role: str | None = Query(None),
    tag: str | None = Query(None),
    include_disabled: bool = Query(False),
):
    agents = registry.list_agents(
        role=role,
        enabled_only=not include_disabled,
        tag=tag,
    )
    return AgentListResponse(agents=agents, total=len(agents))


@router.get("/agents/{agent_id}", summary="Получить агента")
def get_agent(agent_id: str):
    agent = registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Агент '{agent_id}' не найден")
    return agent


@router.patch("/agents/{agent_id}", summary="Обновить агента")
def update_agent(agent_id: str, body: AgentUpdate):
    existing = registry.get_agent(agent_id)
    if not existing:
        raise HTTPException(404, f"Агент '{agent_id}' не найден")
    updates = body.model_dump(exclude_none=True)
    return registry.update_agent(agent_id, updates)


@router.delete("/agents/{agent_id}", summary="Удалить агента (soft)")
def delete_agent(agent_id: str):
    existing = registry.get_agent(agent_id)
    if not existing:
        raise HTTPException(404, f"Агент '{agent_id}' не найден")
    return registry.delete_agent(agent_id)


# ── Состояние агента ──────────────────────────────────────────

@router.get("/agents/{agent_id}/state", summary="Получить состояние агента")
def get_agent_state(agent_id: str):
    return registry.get_agent_state(agent_id)


@router.put("/agents/{agent_id}/state", summary="Сохранить состояние агента")
def set_agent_state(agent_id: str, body: AgentStateData):
    return registry.set_agent_state(agent_id, body.state)


# ── История запусков ──────────────────────────────────────────

@router.get("/agents/{agent_id}/runs", summary="История запусков агента", response_model=AgentRunsResponse)
def get_agent_runs(
    agent_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    runs, total = registry.get_agent_runs(agent_id, limit=limit, offset=offset)
    return AgentRunsResponse(runs=runs, total=total)


@router.post("/agents/{agent_id}/runs", summary="Записать результат запуска")
def record_run(agent_id: str, body: AgentRunRecord):
    data = body.model_dump()
    data["agent_id"] = agent_id
    return registry.record_agent_run(data)
