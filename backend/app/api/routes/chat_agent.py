from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.application.chat.local_chat import run_chat, run_chat_stream
from app.application.chat_agent import project_tools, state, web_tools
from app.application.chat_agent.project_context import build_chat_project_context
from app.application.local_models import get_models, list_local_models


router = APIRouter(prefix="/api/chat-agent", tags=["chat-agent"])


class ChatCreateRequest(BaseModel):
    title: str = "New chat"


class ChatPatchRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    memory_saved: bool | None = None


class ChatMessageRequest(BaseModel):
    chat_id: int | None = None
    role: str = "user"
    content: str = Field(..., min_length=1)


class SettingsRequest(BaseModel):
    context_window: int = 131072
    default_model: str = "local-model"
    agent_profile: str = "default"


class ChatAgentRequest(BaseModel):
    model_name: str = "local-model"
    profile_name: str = "default"
    user_input: str
    session_id: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    num_ctx: int = 131072


class ProjectOpenRequest(BaseModel):
    path: str
    name: str = ""


class ProjectActivateRequest(BaseModel):
    id: str


class ProjectReadRequest(BaseModel):
    path: str
    max_chars: int = 20000


class ProjectSearchRequest(BaseModel):
    query: str
    max_results: int = 50


class MemoryCreateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    category: str = "fact"
    source: str = "manual"
    importance: int = Field(default=5, ge=1, le=10)


class MemorySearchRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=20, ge=1, le=50)


def _json(data: Any) -> JSONResponse:
    return JSONResponse(content=jsonable_encoder(data), media_type="application/json; charset=utf-8")


def _server_context_window(model_name: str) -> int | None:
    try:
        payload = get_models()
    except Exception:
        return None
    models = payload.get("models") if isinstance(payload, dict) and isinstance(payload.get("models"), list) else []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if name != model_name:
            continue
        for key in ("context_window", "n_ctx", "context_length", "max_context_length"):
            try:
                value = int(item.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
    return None


def _effective_context_window(requested: int, model_name: str) -> int:
    base = max(1024, int(requested or 131072))
    server_limit = _server_context_window(model_name)
    return min(base, server_limit) if server_limit else base


def _active_project_root() -> str:
    return str(state.active_project().get("path") or "")


def _direct_history(payload: ChatAgentRequest) -> list[dict[str, Any]]:
    history = list(payload.history or [])
    if not history:
        return history
    last = history[-1]
    if (
        isinstance(last, dict)
        and last.get("role") == "user"
        and str(payload.user_input).strip().startswith(str(last.get("content") or "").strip())
    ):
        return history[:-1]
    return history


def _build_task_context(project_root: str, user_input: str) -> str:
    context = build_chat_project_context(project_root, user_input)
    policy = (
        "Chat Agent workspace policy:\n"
        "- You may analyze/read the selected Chat project and uploaded document context.\n"
        "- Do not claim Code Agent capabilities, shell access, SSH, MCP, sandbox, or code-edit execution.\n"
        "- If the selected project is empty, state that it is empty and do not suggest code scaffolding unless explicitly asked.\n"
    )
    return (context + "\n\n" + policy).strip() if context else policy


def _build_chat_agent_context(project_root: str, user_input: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    saved_memory = state.capture_memory_from_text(user_input)
    task_context = _build_task_context(project_root, user_input)
    memory_context = state.build_memory_context(user_input)
    if memory_context:
        task_context = (task_context + "\n\n" + memory_context).strip()
    web_context = web_tools.collect_web_context(user_input)
    if web_context.get("context"):
        task_context = (task_context + "\n\n" + str(web_context["context"])).strip()
    memory_meta = {
        "used": bool(memory_context),
        "saved": [{"id": item.get("id"), "text": item.get("text")} for item in saved_memory],
    }
    return task_context, web_context, memory_meta


def _chat_agent_tool_meta(
    web_context: dict[str, Any],
    memory_meta: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    tools: list[str] = []
    tool_results: list[dict[str, Any]] = []
    if memory_meta.get("used") or memory_meta.get("saved"):
        tools.append("memory")
        tool_results.append({
            "tool": "memory",
            "ok": True,
            "used": bool(memory_meta.get("used")),
            "saved": memory_meta.get("saved") if isinstance(memory_meta.get("saved"), list) else [],
        })
    if not web_context.get("attempted"):
        return tools, tool_results
    tool_result = {
        "tool": "web_search",
        "ok": bool(web_context.get("used")),
        "mode": str(web_context.get("mode") or ""),
        "results": web_context.get("results") if isinstance(web_context.get("results"), list) else [],
        "errors": web_context.get("errors") if isinstance(web_context.get("errors"), dict) else {},
    }
    tools.append("web_search")
    tool_results.append(tool_result)
    return tools, tool_results


def _run_chat_agent(payload: ChatAgentRequest) -> dict[str, Any]:
    project_root = _active_project_root()
    task_context, web_context, memory_meta = _build_chat_agent_context(project_root, payload.user_input)
    tools, tool_results = _chat_agent_tool_meta(web_context, memory_meta)
    result = run_chat(
        model_name=payload.model_name,
        profile_name=payload.profile_name,
        user_input=payload.user_input,
        history=_direct_history(payload),
        num_ctx=_effective_context_window(payload.num_ctx, payload.model_name),
        task_context=task_context,
    )
    answer = str(result.get("answer") or "")
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    error = "; ".join(str(item) for item in warnings if item) if result.get("ok") is False else ""
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    meta = {**meta, "agent": "chat-agent", "project_root": project_root, "tools": tools, "memory": memory_meta}
    if error:
        meta["error"] = error
    return {
        "ok": bool(result.get("ok")),
        "answer": answer,
        "content": answer,
        "timeline": [{"step": "chat_agent", "title": "Chat Agent", "status": "error" if error else "done"}],
        "tool_results": tool_results,
        "meta": meta,
    }


@router.get("/models")
async def models():
    return await list_local_models()


@router.get("/settings")
def settings_get():
    result = state.get_settings()
    server_limit = _server_context_window(str(result.get("default_model") or "local-model"))
    if server_limit:
        result["server_context_window"] = server_limit
        result["context_window"] = min(int(result.get("context_window") or 131072), server_limit)
    return result


@router.put("/settings")
def settings_put(payload: SettingsRequest):
    context_window = _effective_context_window(payload.context_window, payload.default_model)
    result = state.save_settings(context_window, payload.default_model, payload.agent_profile)
    server_limit = _server_context_window(result.get("default_model", payload.default_model))
    if server_limit:
        result["server_context_window"] = server_limit
    return result


@router.get("/chats")
def chats_list():
    return {"items": state.list_chats()}


@router.post("/chats")
def chats_create(payload: ChatCreateRequest):
    return state.create_chat(payload.title)


@router.patch("/chats/{chat_id}")
def chats_patch(chat_id: int, payload: ChatPatchRequest):
    item = state.update_chat(chat_id, title=payload.title, pinned=payload.pinned, memory_saved=payload.memory_saved)
    if not item:
        raise HTTPException(status_code=404, detail="chat not found")
    return item


@router.patch("/chats/{chat_id}/pin")
def chats_pin(chat_id: int, payload: ChatPatchRequest):
    item = state.update_chat(chat_id, pinned=payload.pinned)
    if not item:
        raise HTTPException(status_code=404, detail="chat not found")
    return item


@router.patch("/chats/{chat_id}/memory")
def chats_memory(chat_id: int, payload: ChatPatchRequest):
    item = state.update_chat(chat_id, memory_saved=payload.memory_saved)
    if not item:
        raise HTTPException(status_code=404, detail="chat not found")
    return item


@router.delete("/chats/{chat_id}")
def chats_delete(chat_id: int):
    state.delete_chat(chat_id)
    return {"ok": True}


@router.get("/chats/{chat_id}/messages")
def chats_messages(chat_id: int):
    return {"items": state.get_messages(chat_id)}


@router.post("/messages")
def messages_add(payload: ChatMessageRequest):
    return {"status": "ok", **state.add_message(payload.chat_id, payload.role, payload.content)}


@router.get("/memory")
def memory_list(limit: int = Query(default=100, ge=1, le=500)):
    return {"ok": True, "items": state.list_memory(limit)}


@router.get("/memory/stats")
def memory_stats():
    return state.memory_stats()


@router.post("/memory")
def memory_add(payload: MemoryCreateRequest):
    try:
        item = state.add_memory(
            payload.text,
            category=payload.category,
            source=payload.source,
            importance=payload.importance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": item}


@router.post("/memory/search")
def memory_search(payload: MemorySearchRequest):
    return {"ok": True, "items": state.search_memory(payload.query, limit=payload.limit)}


@router.delete("/memory/{memory_id}")
def memory_delete(memory_id: str):
    return {"ok": state.delete_memory(memory_id)}


@router.post("/send")
def send(payload: ChatAgentRequest):
    try:
        return _json(_run_chat_agent(payload))
    except Exception as exc:
        return _json({
            "ok": False,
            "answer": "",
            "content": "",
            "timeline": [{"step": "chat_agent", "title": "Chat Agent", "status": "error", "detail": str(exc)}],
            "tool_results": [],
            "meta": {"agent": "chat-agent", "error": str(exc)},
        })


@router.post("/stream")
def stream(payload: ChatAgentRequest):
    def event_generator():
        try:
            project_root = _active_project_root()
            task_context, web_context, memory_meta = _build_chat_agent_context(project_root, payload.user_input)
            tools, _tool_results = _chat_agent_tool_meta(web_context, memory_meta)
            full_text = ""
            for token in run_chat_stream(
                model_name=payload.model_name,
                profile_name=payload.profile_name,
                user_input=payload.user_input,
                history=_direct_history(payload),
                num_ctx=_effective_context_window(payload.num_ctx, payload.model_name),
                task_context=task_context,
            ):
                full_text += token
                yield f"data: {json.dumps({'token': token, 'done': False}, ensure_ascii=False)}\n\n"
            done = {
                "token": "",
                "done": True,
                "full_text": full_text,
                "meta": {"agent": "chat-agent", "project_root": project_root, "tools": tools, "memory": memory_meta},
                "timeline": [{"step": "chat_agent", "title": "Chat Agent", "status": "done"}],
            }
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        except Exception as exc:
            err = {"error": str(exc), "done": True, "meta": {"agent": "chat-agent"}}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/projects")
def projects_list():
    return {"ok": True, "items": state.list_projects()}


@router.get("/projects/active")
def projects_active():
    return {"ok": True, "project": state.active_project()}


@router.post("/projects")
def projects_open(payload: ProjectOpenRequest):
    try:
        return {"ok": True, "project": state.upsert_project(payload.path, payload.name, active=True)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/active")
def projects_set_active(payload: ProjectActivateRequest):
    project = state.set_active_project(payload.id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True, "project": project}


@router.delete("/projects/active")
def projects_clear_active():
    state.clear_active_project()
    return {"ok": True, "project": None}


@router.delete("/projects/{project_id}")
def projects_remove(project_id: str):
    return {"ok": state.remove_project(project_id)}


@router.get("/projects/tree")
def projects_tree(
    max_depth: int = Query(default=3, ge=1, le=8),
    max_items: int = Query(default=300, ge=1, le=2000),
):
    project_root = str(state.active_project().get("path") or "")
    return project_tools.project_tree(project_root, max_depth=max_depth, max_items=max_items)


@router.post("/projects/read")
def projects_read(payload: ProjectReadRequest):
    project_root = str(state.active_project().get("path") or "")
    return project_tools.read_project_file(project_root, payload.path, max_chars=payload.max_chars)


@router.post("/projects/search")
def projects_search(payload: ProjectSearchRequest):
    project_root = str(state.active_project().get("path") or "")
    return project_tools.search_project(project_root, payload.query, max_results=payload.max_results)
