from typing import Any

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.agents_service import run_agent

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    model_name: str
    profile_name: str = "default"
    user_input: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    use_memory: bool = True
    use_library: bool = True


@router.post("/send")
def chat_send(payload: ChatRequest):
    try:
        result = run_agent(
            model_name=payload.model_name,
            profile_name=payload.profile_name,
            user_input=payload.user_input,
            use_memory=payload.use_memory,
            use_library=payload.use_library,
            history=payload.history,
        )
        return JSONResponse(
            content=jsonable_encoder(result),
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        fallback = {
            "ok": False,
            "answer": "",
            "timeline": [
                {
                    "step": "chat_route_error",
                    "title": "Ошибка route /api/chat/send",
                    "status": "error",
                    "detail": str(exc),
                }
            ],
            "tool_results": [],
            "meta": {
                "error": str(exc),
                "route": "/api/chat/send",
            },
        }
        return JSONResponse(
            content=jsonable_encoder(fallback),
            media_type="application/json; charset=utf-8",
        )
