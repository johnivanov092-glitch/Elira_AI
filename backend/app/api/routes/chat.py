"""
chat.py — чат-роуты: обычный /send + SSE-стриминг /stream + /attach (вложения)
"""
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, PrivateAttr

from app.application.file_extract.runtime import extract_file
from app.infrastructure.llm.vision_ocr import (
    describe_image,
    is_vision_enabled,
)

# Расширения, которые отправляем в локальную vision-модель (:8004),
# остальное идёт через extract_file (текст / OCR сканов через :8002).
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
# Лимит на один файл — крупные сканы режем заранее, чтобы не упереться в RAM.
_MAX_ATTACH_BYTES = 25 * 1024 * 1024

from app.application.chat.planner_v2 import (
    PlannerV2Service,
    get_defaults_as_strings as planner_get_defaults,
    refresh_planner,
)
from app.application.chat.cancellation import (
    register_run,
    request_cancel,
    unregister_run,
)
from app.application.chat.runtime import run_agent, run_agent_stream
from app.application.chat.local_chat import (
    resolve_profile_name,
    run_chat,
    run_chat_stream,
)
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
    num_ctx: int = 131072
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
    # Идентификатор прогона для отмены (#2). В planner-режиме сервер генерит свой
    # run_id и шлёт его первым SSE-событием; в direct_llm фронт задаёт его здесь,
    # чтобы кнопка Стоп могла адресовать конкретный запрос через /api/chat/cancel.
    run_id: str | None = None
    # Вложения: извлечённый текст / описания картинок, подмешиваются в user_input
    # на стороне фронта; поле зарезервировано для совместимости и будущей логики.
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    _resolved_profile: str | None = PrivateAttr(default=None)

    def effective_profile(self) -> str:
        """Persona the request should actually run with.

        The frontend hardcodes profile_name="default"; resolving here makes the
        profile saved in Settings take effect in both chat paths (direct +
        planner) and /send. Cached so the settings file is read once per request.
        """
        if self._resolved_profile is None:
            self._resolved_profile = resolve_profile_name(self.profile_name)
        return self._resolved_profile


def _direct_meta(payload: ChatRequest) -> dict[str, Any]:
    return {
        "model_name": payload.model_name,
        "profile_name": payload.effective_profile(),
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
        profile_name=payload.effective_profile(),
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
            profile_name=payload.effective_profile(),
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
                # Direct path: register the client-supplied run_id so Stop can
                # cancel it (#2); usage_sink carries final tokens/sec (#1).
                direct_run_id = str(payload.run_id or "")
                cancel_event = register_run(direct_run_id) if direct_run_id else None
                if direct_run_id:
                    yield f"data: {json.dumps({'run_id': direct_run_id, 'done': False}, ensure_ascii=False)}\n\n"
                usage_sink: dict[str, Any] = {}
                try:
                    for token in run_chat_stream(
                        model_name=payload.model_name,
                        profile_name=payload.effective_profile(),
                        user_input=payload.user_input,
                        history=_direct_history(payload),
                        num_ctx=payload.num_ctx,
                        usage_sink=usage_sink,
                        cancel_event=cancel_event,
                    ):
                        if cancel_event is not None and cancel_event.is_set():
                            break
                        full_text += token
                        yield f"data: {json.dumps({'token': token, 'done': False}, ensure_ascii=False)}\n\n"
                finally:
                    if direct_run_id:
                        unregister_run(direct_run_id)
                if usage_sink:
                    usage_event = {
                        "usage": {
                            "prompt_tokens": usage_sink.get("prompt_tokens", 0),
                            "completion_tokens": usage_sink.get("completion_tokens", 0),
                            "total_tokens": usage_sink.get("total_tokens", 0),
                            "tokens_per_second": round(
                                float(usage_sink.get("tokens_per_second") or 0.0), 1
                            ),
                            "estimated": False,
                        },
                        "done": False,
                    }
                    yield f"data: {json.dumps(usage_event, ensure_ascii=False)}\n\n"
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
                profile_name=payload.effective_profile(),
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


class CancelRequest(BaseModel):
    run_id: str


@router.post("/cancel")
def chat_cancel(payload: CancelRequest) -> dict[str, Any]:
    """Остановить живую генерацию (#2).

    Фронт шлёт run_id, полученный первым SSE-событием стрима. Мы выставляем
    cancel_event в реестре — стрим-цикл видит его между токенами, выходит и
    закрывает upstream-соединение к llama-server, чтобы тот не продолжал
    генерировать «в пустоту» после нажатия Стоп.
    """
    cancelled = request_cancel(payload.run_id)
    return {"ok": cancelled, "run_id": payload.run_id}


# ── вложения: картинка → vision (:8004), документ → extract_file (+OCR :8002) ──
def _attach_result(
    *, filename: str, kind: str, text: str, note: str = "", ok: bool = True
) -> dict[str, Any]:
    """Единая форма ответа /attach: фронт подмешивает `text` в user_input."""
    return {
        "ok": ok,
        "filename": filename,
        "kind": kind,          # "image" | "document"
        "text": text,          # извлечённый текст / описание картинки
        "chars": len(text),
        "note": note,          # необязательная пометка (для UI/диагностики)
    }


@router.post("/attach")
async def chat_attach(file: UploadFile):
    """Принять одно вложение и вернуть извлечённый текст.

    Картинки уходят в локальную vision-модель (:8004), всё остальное — в
    extract_file (текст напрямую, сканы PDF — через локальный OCR :8002).
    Внешние сервисы не используются. Сам файл нигде не сохраняется —
    возвращаем только текст, который фронт подмешает в сообщение.
    """
    filename = file.filename or "файл"
    ext = Path(filename).suffix.lower()
    try:
        contents = await file.read()
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content=jsonable_encoder(
                _attach_result(
                    filename=filename, kind="document", text="", ok=False,
                    note=f"Не удалось прочитать файл: {exc}",
                )
            ),
            media_type="application/json; charset=utf-8",
        )

    if not contents:
        return JSONResponse(
            status_code=400,
            content=jsonable_encoder(
                _attach_result(
                    filename=filename, kind="document", text="", ok=False,
                    note="Пустой файл",
                )
            ),
            media_type="application/json; charset=utf-8",
        )

    if len(contents) > _MAX_ATTACH_BYTES:
        return JSONResponse(
            status_code=413,
            content=jsonable_encoder(
                _attach_result(
                    filename=filename, kind="document", text="", ok=False,
                    note=f"Файл больше {_MAX_ATTACH_BYTES // (1024 * 1024)} МБ",
                )
            ),
            media_type="application/json; charset=utf-8",
        )

    # ── картинка → vision ────────────────────────────────────────
    if ext in _IMAGE_EXTS:
        if not is_vision_enabled():
            return JSONResponse(
                content=jsonable_encoder(
                    _attach_result(
                        filename=filename, kind="image", text="", ok=False,
                        note="Распознавание картинок отключено на сервере (VISION_ENABLED).",
                    )
                ),
                media_type="application/json; charset=utf-8",
            )
        description = describe_image(filename, contents)
        if not description:
            return JSONResponse(
                content=jsonable_encoder(
                    _attach_result(
                        filename=filename, kind="image", text="", ok=False,
                        note="Не удалось распознать изображение.",
                    )
                ),
                media_type="application/json; charset=utf-8",
            )
        return JSONResponse(
            content=jsonable_encoder(
                _attach_result(filename=filename, kind="image", text=description)
            ),
            media_type="application/json; charset=utf-8",
        )

    # ── документ → extract_file (текст / OCR сканов через :8002) ──
    # extract_file всегда отдаёт ok=True, а сбои кладёт в text как "[... ошибка: ...]".
    extracted = extract_file(filename, contents)
    text = str(extracted.get("text") or "")
    stripped = text.strip()
    if not stripped:
        return JSONResponse(
            content=jsonable_encoder(
                _attach_result(
                    filename=filename, kind="document", text="", ok=False,
                    note="В файле не найдено текста.",
                )
            ),
            media_type="application/json; charset=utf-8",
        )
    # маркеры ошибок экстракторов: "[PDF ошибка: ...]", "[DOCX не установлен: ...]" и т.п.
    if stripped.startswith("[") and ("ошибка" in stripped.lower() or "не установлен" in stripped.lower()):
        return JSONResponse(
            content=jsonable_encoder(
                _attach_result(
                    filename=filename, kind="document", text="", ok=False,
                    note=stripped.strip("[]"),
                )
            ),
            media_type="application/json; charset=utf-8",
        )
    return JSONResponse(
        content=jsonable_encoder(
            _attach_result(filename=filename, kind="document", text=text)
        ),
        media_type="application/json; charset=utf-8",
    )
