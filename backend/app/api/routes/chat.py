"""
chat.py — чат-роуты: обычный /send + SSE-стриминг /stream
"""
import json
from typing import Any

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.application.chat.planner_v2 import (
    PlannerV2Service,
    get_defaults_as_strings as planner_get_defaults,
    refresh_planner,
)
from app.application.chat.runtime import run_agent, run_agent_stream
from app.application.chat.ollama_chat import run_chat, run_chat_stream
from app.application.elira_memory.settings import (
    get_planner_keywords,
    save_planner_keywords,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ClassifyRequest(BaseModel):
    query: str


@router.post("/classify")
def classify(payload: ClassifyRequest) -> dict[str, Any]:
    """Debug helper: returns PlannerV2's routing plan for a query
    WITHOUT calling any LLM. Useful when tuning keyword bags — write
    a query, see what route + tools + scores come out.
    """
    plan = PlannerV2Service().plan(payload.query)
    return plan


class KeywordsWriteRequest(BaseModel):
    keywords: dict[str, list[str]] = Field(default_factory=dict)


@router.get("/keywords")
def get_keywords() -> dict[str, Any]:
    """Return the effective keyword bags used by the planner.

    Shape: {
      "effective": {route: [str, str, ...]},  # what planner actually uses
      "user":      {route: [str, ...]},        # what's saved in DB (subset)
      "defaults":  {route: [str, ...]},        # shipped defaults (full)
    }
    """
    user = get_planner_keywords()
    defaults = planner_get_defaults()
    effective: dict[str, list[str]] = {}
    for route, default_list in defaults.items():
        if user.get(route):
            effective[route] = user[route]
        else:
            effective[route] = default_list
    return {"effective": effective, "user": user, "defaults": defaults}


@router.put("/keywords")
def put_keywords(payload: KeywordsWriteRequest) -> dict[str, Any]:
    """Persist user keyword overrides and immediately recompile the
    planner. Pass {} to revert to shipped defaults.
    """
    saved = save_planner_keywords(payload.keywords)
    summary = refresh_planner(saved)
    return {"ok": True, "saved": saved, "active_counts": summary}


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
    direct_llm: bool = False


def _direct_meta(payload: ChatRequest) -> dict[str, Any]:
    return {
        "model_name": payload.model_name,
        "profile_name": payload.profile_name,
        "route": "direct_llm",
        "tools": [],
        "direct_llm": True,
    }


def _direct_timeline(status: str = "done", detail: str = "") -> list[dict[str, Any]]:
    item: dict[str, Any] = {
        "step": "direct_llm",
        "title": "Direct LLM",
        "status": status,
    }
    if detail:
        item["detail"] = detail
    return [item]


def _direct_history(payload: ChatRequest) -> list[dict[str, Any]]:
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


def _run_direct_chat(payload: ChatRequest) -> dict[str, Any]:
    result = run_chat(
        model_name=payload.model_name,
        profile_name=payload.profile_name,
        user_input=payload.user_input,
        history=_direct_history(payload),
        num_ctx=payload.num_ctx,
    )
    answer = str(result.get("answer") or "")
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    error = "; ".join(str(item) for item in warnings if item) if result.get("ok") is False else ""
    meta = {**_direct_meta(payload), **(result.get("meta") if isinstance(result.get("meta"), dict) else {})}
    if error:
        meta["error"] = error
    return {
        "ok": bool(result.get("ok")),
        "answer": answer,
        "content": answer,
        "timeline": _direct_timeline("error" if error else "done", error),
        "tool_results": [],
        "meta": meta,
    }


# ── обычный запрос (без стриминга) ──────────────────────────────
@router.post("/send")
def chat_send(payload: ChatRequest):
    try:
        if payload.direct_llm:
            result = _run_direct_chat(payload)
            return JSONResponse(
                content=jsonable_encoder(result),
                media_type="application/json; charset=utf-8",
            )

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
            if payload.direct_llm:
                full_text = ""
                for token in run_chat_stream(
                    model_name=payload.model_name,
                    profile_name=payload.profile_name,
                    user_input=payload.user_input,
                    history=_direct_history(payload),
                    num_ctx=payload.num_ctx,
                ):
                    full_text += token
                    yield f"data: {json.dumps({'token': token, 'done': False}, ensure_ascii=False)}\n\n"
                done_event = {
                    "token": "",
                    "done": True,
                    "full_text": full_text,
                    "meta": _direct_meta(payload),
                    "timeline": _direct_timeline(),
                }
                yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"
                return

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
