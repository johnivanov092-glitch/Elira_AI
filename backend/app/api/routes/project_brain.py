"""Project Brain routes — thin HTTP layer for /api/project-brain.

All business logic lives in app.application.agents.ollama_agent_service.
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.application.agents.ollama_agent_service import (
    LEGACY_AGENT_CATALOG,
    MAX_ATTACHMENT_BYTES,
    MAX_READ_BYTES,
    PROJECT_ROOT,
    UPLOAD_ROOT,
    attach_project_file,
    attachment_summary,
    execute_chat_send,
    execute_ollama_plan,
    execute_ollama_run,
    hash_bytes,
    looks_text_file,
    project_file_snapshot,
    read_text_file,
    resolve_project_file,
    store_attachment,
)
from app.infrastructure.db.memory import vector_memory_capability_status
from app.infrastructure.runtime.ollama_client import (
    OLLAMA_BASE_URL,
    fetch_ollama_tags,
    pick_model,
)
from app.application.skills.skills_service import screenshot_capability_status

router = APIRouter(prefix="/api/project-brain", tags=["project-brain"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status")
def project_brain_status():
    return {
        "status": "ok",
        "project_root": str(PROJECT_ROOT),
        "chat_upload_root": str(UPLOAD_ROOT),
        "max_read_bytes": MAX_READ_BYTES,
        "capabilities": {
            "vector_memory": vector_memory_capability_status(),
            "screenshot": screenshot_capability_status(),
        },
    }


@router.get("/snapshot")
def project_snapshot():
    files = project_file_snapshot()
    return {"status": "ok", "project_root": str(PROJECT_ROOT), "files": files, "files_count": len(files)}


@router.get("/file")
def read_project_file(path: str = Query(..., min_length=1)):
    try:
        full_path, rel_path = resolve_project_file(path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not looks_text_file(full_path):
        raise HTTPException(status_code=415, detail="Only text-like source files are readable")
    size = full_path.stat().st_size
    if size > MAX_READ_BYTES:
        raise HTTPException(status_code=413, detail=f"File is too large to open ({size} bytes)")
    try:
        content, encoding, raw = read_text_file(full_path)
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Cannot read file: {exc}") from exc
    return {
        "status": "ok",
        "path": str(rel_path).replace("\\", "/"),
        "name": full_path.name,
        "suffix": full_path.suffix.lower(),
        "size": size,
        "encoding": encoding,
        "sha256": hash_bytes(raw),
        "content": content,
    }


@router.get("/agent/legacy/catalog")
def legacy_agents_catalog():
    return {"status": "ok", "agents": LEGACY_AGENT_CATALOG}


@router.get("/agent/ollama/status")
def ollama_status():
    tags = fetch_ollama_tags()
    model_names = [item.get("name") for item in tags.get("models") or [] if item.get("name")]
    default_model = pick_model(None, tags) if model_names or True else ""
    return {
        "status": "ok",
        "provider": "ollama",
        "base_url": OLLAMA_BASE_URL,
        "models": model_names,
        "default_model": default_model,
    }


@router.post("/chat/attachment")
async def upload_chat_attachment(file: UploadFile = File(...), source: str = Form("upload")):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail=f"Attachment too large ({len(data)} bytes)")
    item = store_attachment(file.filename or "attachment.bin", data, source=source)
    return {"status": "ok", "attachment": attachment_summary(item)}


@router.post("/chat/project-file")
def route_attach_project_file(path: str = Form(...)):
    item = attach_project_file(path)
    return {"status": "ok", "attachment": attachment_summary(item) | {"project_path": item["project_path"]}}


@router.post("/chat/send")
def chat_send(payload: ChatRequest):
    return execute_chat_send(
        message=payload.message,
        model_hint=payload.model,
        mode=payload.mode,
        web_enabled=payload.web_enabled,
        session_id=payload.session_id,
        attachment_ids=payload.attachment_ids,
        selected_project_paths=payload.selected_project_paths,
    )


@router.post("/agent/ollama/plan")
def ollama_agent_plan(payload: LocalAgentPlanRequest):
    return execute_ollama_plan(
        model_hint=payload.model,
        goal=payload.goal,
        selected_path=payload.selected_path,
        selected_content=payload.selected_content,
    )


@router.post("/agent/ollama/run")
def ollama_agent_run(payload: LocalAgentRunRequest):
    return execute_ollama_run(
        model_hint=payload.model,
        goal=payload.goal,
        selected_path=payload.selected_path,
        selected_content=payload.selected_content,
        project_files=payload.project_files,
        mode=payload.mode,
    )
