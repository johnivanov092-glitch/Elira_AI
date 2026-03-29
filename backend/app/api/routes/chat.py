"""
chat.py — чат-роуты: обычный /send + SSE-стриминг /stream
"""
import json
from typing import Any

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.services.agents_service import run_agent, run_agent_stream

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    model_name: str
    profile_name: str = "default"
    user_input: str
    session_id: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    num_ctx: int = 8192
    use_memory: bool = True
    use_library: bool = True
    use_reflection: bool = False
    # Скиллы — явные флаги (по умолчанию True для обратной совместимости)
    use_web_search: bool = True
    use_python_exec: bool = True
    use_image_gen: bool = True
    use_file_gen: bool = True
    use_http_api: bool = True
    use_sql: bool = True
    use_screenshot: bool = True
    use_encrypt: bool = True
    use_archiver: bool = True
    use_converter: bool = True
    use_regex: bool = True
    use_translator: bool = True
    use_csv: bool = True
    use_webhook: bool = True
    use_plugins: bool = True


# ── обычный запрос (без стриминга) ──────────────────────────────
@router.post("/send")
def chat_send(payload: ChatRequest):
    try:
        result = run_agent(
            model_name=payload.model_name,
            profile_name=payload.profile_name,
            user_input=payload.user_input,
            session_id=payload.session_id,
            use_memory=payload.use_memory,
            use_library=payload.use_library,
            use_reflection=payload.use_reflection,
            history=payload.history,
            num_ctx=payload.num_ctx,
            use_web_search=payload.use_web_search,
            use_python_exec=payload.use_python_exec,
            use_image_gen=payload.use_image_gen,
            use_file_gen=payload.use_file_gen,
            use_http_api=payload.use_http_api,
            use_sql=payload.use_sql,
            use_screenshot=payload.use_screenshot,
            use_encrypt=payload.use_encrypt,
            use_archiver=payload.use_archiver,
            use_converter=payload.use_converter,
            use_regex=payload.use_regex,
            use_translator=payload.use_translator,
            use_csv=payload.use_csv,
            use_webhook=payload.use_webhook,
            use_plugins=payload.use_plugins,
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


# ── SSE-стриминг ────────────────────────────────────────────────
@router.post("/stream")
def chat_stream(payload: ChatRequest):
    """
    Server-Sent Events: каждый токен отправляется как `data: {...}\n\n`.
    Финальный пакет содержит `"done": true` и полные метаданные.
    """

    def event_generator():
        try:
            for event in run_agent_stream(
                model_name=payload.model_name,
                profile_name=payload.profile_name,
                user_input=payload.user_input,
                session_id=payload.session_id,
                use_memory=payload.use_memory,
                use_library=payload.use_library,
                use_reflection=payload.use_reflection,
                history=payload.history,
                num_ctx=payload.num_ctx,
                use_web_search=payload.use_web_search,
                use_python_exec=payload.use_python_exec,
                use_image_gen=payload.use_image_gen,
                use_file_gen=payload.use_file_gen,
                use_http_api=payload.use_http_api,
                use_sql=payload.use_sql,
                use_screenshot=payload.use_screenshot,
                use_encrypt=payload.use_encrypt,
                use_archiver=payload.use_archiver,
                use_converter=payload.use_converter,
                use_regex=payload.use_regex,
                use_translator=payload.use_translator,
                use_csv=payload.use_csv,
                use_webhook=payload.use_webhook,
                use_plugins=payload.use_plugins,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            error_event = {
                "done": True,
                "error": str(exc),
                "token": "",
                "full_text": "",
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
