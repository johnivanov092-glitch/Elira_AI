from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.application.elira_memory.service import (
    init_db,
    list_chats,
    create_chat,
    update_chat,
    set_chat_pinned,
    set_chat_memory_saved,
    delete_chat,
    get_messages,
    add_message,
)
from app.application.elira_memory.settings import get_settings, save_settings
from app.application.feature_flags import get_flags, set_flag
from app.application.local_models import list_local_models

router = APIRouter(prefix="/api/elira", tags=["elira-state"])


class ChatCreateRequest(BaseModel):
    title: str = "Новый чат"


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
    agent_profile: str = "Авто"
    route_model_map: dict | None = None
    orchestration_enabled: bool = False


class FeatureFlagRequest(BaseModel):
    """Toggle one deferred-track feature flag (D1 remote MCP / D3 envelopes /
    proactivity / R1 catalog assist). Keep this Literal in sync with
    feature_flags._ENV_VAR — a missing entry makes the UI toggle 422 silently
    (the action_envelopes bug, repeated for catalog_assist in the R1 review)."""

    model_config = {"extra": "forbid"}
    name: Literal["remote_mcp", "action_envelopes", "proactive", "catalog_assist", "web_corpus"]
    value: bool


@router.get("/models")
async def models():
    return await list_local_models()


@router.get("/settings")
def settings_get():
    init_db()
    return get_settings()


@router.put("/settings")
def settings_put(payload: SettingsRequest):
    init_db()
    return save_settings(
        payload.context_window,
        payload.default_model,
        payload.agent_profile,
        payload.route_model_map,
        payload.orchestration_enabled,
    )


@router.get("/feature-flags")
def feature_flags_get():
    # Effective state (env override applied) of the deferred-track flags.
    return get_flags()


@router.put("/feature-flags")
def feature_flags_put(payload: FeatureFlagRequest):
    # Persists to data/feature_flags.json; returns the new effective state.
    # An ELIRA_* env override, if set, keeps winning and is reflected here.
    return set_flag(payload.name, payload.value)


@router.get("/chats")
def chats_list():
    init_db()
    return {"items": list_chats()}


@router.post("/chats")
def chats_create(payload: ChatCreateRequest):
    init_db()
    return create_chat(payload.title)


@router.patch("/chats/{chat_id}")
def chats_patch(chat_id: int, payload: ChatPatchRequest):
    init_db()
    item = update_chat(
        chat_id,
        title=payload.title,
        pinned=payload.pinned,
        memory_saved=payload.memory_saved,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return item


@router.patch("/chats/{chat_id}/pin")
def chats_pin(chat_id: int, payload: ChatPatchRequest):
    init_db()
    item = set_chat_pinned(chat_id, bool(payload.pinned))
    if not item:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return item


@router.patch("/chats/{chat_id}/memory")
def chats_memory(chat_id: int, payload: ChatPatchRequest):
    init_db()
    item = set_chat_memory_saved(chat_id, bool(payload.memory_saved))
    if not item:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return item


@router.delete("/chats/{chat_id}")
def chats_delete(chat_id: int):
    init_db()
    delete_chat(chat_id)
    return {"status": "ok"}


@router.get("/chats/{chat_id}/messages")
def chats_messages(chat_id: int):
    init_db()
    return {"items": get_messages(chat_id)}


@router.post("/chats/{chat_id}/reflect")
def chats_reflect(chat_id: int):
    """Consolidate this chat into a durable episodic memory (reflection) so it
    can be recalled in future chats. Marks the chat as saved on success."""
    init_db()
    from app.application.rag_memory.service import reflect_chat

    return reflect_chat(chat_id)


@router.post("/messages")
def messages_add(payload: ChatMessageRequest):
    init_db()
    chat_id = payload.chat_id
    if not chat_id:
        created = create_chat("Новый чат")
        chat_id = int(created["id"])
    message = add_message(chat_id, payload.role, payload.content)
    return {"status": "ok", "chat_id": chat_id, "message": message}

