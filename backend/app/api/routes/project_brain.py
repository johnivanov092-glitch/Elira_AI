from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.application.project_brain import chat as project_brain_chat
from app.application.project_brain import files as project_brain_files
from app.application.project_brain import ollama as project_brain_ollama
from app.application.project_brain import state as project_brain_state
from app.application.project_brain import uploads as project_brain_uploads


router = APIRouter(prefix="/api/project-brain", tags=["project-brain"])

LEGACY_AGENT_CATALOG = [
    {
        "id": "chat_agent",
        "title": "Chat agent",
        "kind": "conversation",
        "description": "Базовый диалоговый агент для обычных запросов.",
    },
    {
        "id": "planner_agent",
        "title": "Planner agent",
        "kind": "planning",
        "description": "Пошаговый план и orchestration поверх reasoning/browser/terminal.",
    },
    {
        "id": "browser_agent",
        "title": "Browser agent",
        "kind": "research",
        "description": "Веб-поиск, чтение страниц и сбор контекста для ответа.",
    },
    {
        "id": "coder_agent",
        "title": "Coder agent",
        "kind": "code",
        "description": "Локальный кодовый агент для файла, diff-preview и безопасного patch-flow.",
    },
    {
        "id": "task_graph",
        "title": "Task graph",
        "kind": "orchestration",
        "description": "Граф выполнения шагов для research/code/file режимов.",
    },
    {
        "id": "multi_agent",
        "title": "Multi-agent",
        "kind": "orchestration",
        "description": "Planner + Researcher + Coder + Reviewer + Orchestrator.",
    },
    {
        "id": "reflection_v2",
        "title": "Reflection v2",
        "kind": "quality",
        "description": "Самопроверка ответа, groundedness, completeness, retry loop.",
    },
    {
        "id": "self_improve",
        "title": "Self-improving agent",
        "kind": "quality",
        "description": "Повторное улучшение ответа после критики.",
    },
    {
        "id": "terminal",
        "title": "Terminal",
        "kind": "tool",
        "description": "Безопасный локальный терминальный контекст только для read-only анализа.",
    },
    {
        "id": "image_generation",
        "title": "Image generation",
        "kind": "media",
        "description": "Наследуемый image-flow из старого Elira: routing и prompt prep для будущей генерации.",
    },
]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    model: str | None = Field(default=None, max_length=200)
    mode: str = Field(default="auto", max_length=64)
    web_enabled: bool = Field(default=True)
    session_id: str | None = Field(default=None, max_length=100)
    attachment_ids: list[str] = Field(default_factory=list)
    selected_project_paths: list[str] = Field(default_factory=list)


class LocalAgentRunRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000)
    selected_path: str = Field(..., min_length=1)
    selected_content: str = Field(..., min_length=1, max_length=400_000)
    model: str | None = Field(default=None, max_length=200)
    project_files: list[str] = Field(default_factory=list)
    mode: str = Field(default="patch", max_length=64)


class LocalAgentPlanRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000)
    selected_path: str = Field(..., min_length=1)
    selected_content: str = Field(..., min_length=1, max_length=400_000)
    model: str | None = Field(default=None, max_length=200)


@router.get("/status")
def project_brain_status():
    from app.application.memory.search import vector_memory_capability_status
    from app.application.skills import screenshot_capability_status

    return {
        "status": "ok",
        "project_root": str(project_brain_state.PROJECT_ROOT),
        "excluded_parts": sorted(project_brain_state.EXCLUDED_PARTS),
        "max_read_bytes": project_brain_state.MAX_READ_BYTES,
        "chat_upload_root": str(project_brain_state.UPLOAD_ROOT),
        "capabilities": {
            "vector_memory": vector_memory_capability_status(),
            "screenshot": screenshot_capability_status(),
        },
    }


@router.get("/snapshot")
def project_snapshot():
    return project_brain_files.snapshot_project_files()


@router.get("/file")
def read_project_file(path: str = Query(..., min_length=1)):
    return project_brain_files.read_project_file_payload(path)


@router.get("/agent/legacy/catalog")
def legacy_agents_catalog():
    return {"status": "ok", "agents": LEGACY_AGENT_CATALOG}


@router.get("/agent/ollama/status")
def ollama_status():
    return project_brain_ollama.ollama_status_payload()


@router.post("/chat/attachment")
async def upload_chat_attachment(
    file: UploadFile = File(...),
    source: str = Form("upload"),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(data) > project_brain_state.MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Attachment too large ({len(data)} bytes)",
        )
    item = project_brain_uploads.store_attachment(
        file.filename or "attachment.bin",
        data,
        source=source,
    )
    return {"status": "ok", "attachment": project_brain_uploads.attachment_summary(item)}


@router.post("/chat/project-file")
def attach_project_file(path: str = Form(...)):
    item = project_brain_files.attach_project_file(path)
    return {
        "status": "ok",
        "attachment": project_brain_uploads.attachment_summary(item)
        | {"project_path": item["project_path"]},
    }


@router.post("/chat/send")
def chat_send(payload: ChatRequest):
    return project_brain_chat.send_chat_message(
        message=payload.message,
        model_name=payload.model,
        mode=payload.mode,
        web_enabled=payload.web_enabled,
        session_id=payload.session_id,
        attachment_ids=payload.attachment_ids,
        selected_project_paths=payload.selected_project_paths,
    )


@router.post("/agent/ollama/plan")
def ollama_agent_plan(payload: LocalAgentPlanRequest):
    return project_brain_chat.run_local_agent_plan(
        goal=payload.goal,
        selected_path=payload.selected_path,
        selected_content=payload.selected_content,
        model_name=payload.model,
    )


@router.post("/agent/ollama/run")
def ollama_agent_run(payload: LocalAgentRunRequest):
    return project_brain_chat.run_local_agent(
        goal=payload.goal,
        selected_path=payload.selected_path,
        selected_content=payload.selected_content,
        model_name=payload.model,
        project_files=payload.project_files,
        mode=payload.mode,
    )
