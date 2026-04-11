"""
agents_service.py v8

Улучшения v8:
  • Авто-выбор модели под задачу (route → лучшая модель)
  • Кэширование ответов (SQLite, TTL 2 часа)
  • Умная обрезка истории (релевантные сообщения, не просто последние N)
  • Детальные фазы стриминга
"""
# Legacy monolith: keep behavior stable and prefer extraction into
# application/domain/infrastructure modules over new feature work here.
from __future__ import annotations

import re
import logging
from functools import partial
from typing import Any, Generator

from app.application.chat.context_builder import (
    collect_context as build_chat_context,
    strip_frontend_project_context as build_strip_frontend_project_context,
)
from app.application.chat.service import (
    build_disabled_skills,
    build_task_context,
    prepare_chat_plan,
)
from app.application.chat.agent_os import (
    emit_agent_os_event as _app_emit_agent_os_event,
    record_agent_os_monitoring as _app_record_agent_os_monitoring,
    resolve_agent_os_source_id as _app_resolve_agent_os_source_id,
)
from app.application.chat.finalization import (
    finalize_chat_failure as _app_finalize_chat_failure,
    finalize_chat_success as _app_finalize_chat_success,
    finalize_stream_success as _app_finalize_stream_success,
)
from app.application.chat.stream_service import (
    iter_text_stream_events,
)
from app.infrastructure.search.web_search import (
    build_single_web_subquery_context as _infra_build_single_web_subquery_context,
    clean_query as _infra_clean_query,
    do_temporal_web_search as _infra_do_temporal_web_search,
    do_temporal_web_search_legacy as _infra_do_temporal_web_search_legacy,
    do_web_search as _infra_do_web_search,
    do_web_search_legacy as _infra_do_web_search_legacy,
    get_web_search_result as _infra_get_web_search_result,
    is_strict_web_only_query as _infra_is_strict_web_only_query,
)
from app.services.agent_monitor import record_agent_run_metric
from app.services.agent_sandbox import (
    SandboxPolicyError,
    preflight_or_raise,
    resolve_effective_agent_id,
)
from app.services.chat_service import run_chat, run_chat_stream
from app.services.identity_guard import guard_identity_response
from app.services.planner_v2_service import PlannerV2Service
from app.services.provenance_guard import guard_provenance_response
from app.services.reflection_loop_service import run_reflection_loop
from app.services.run_history_service import RunHistoryService
from app.services.temporal_intent import detect_temporal_intent
from app.services.tool_service import run_tool
from app.services.smart_memory import extract_and_save, get_relevant_context, is_memory_command
from app.services.response_cache import get_cached, set_cached, should_cache
from app.core.config import pick_model_for_route, DEFAULT_MODEL

# RAG память (опционально — если embedding модель доступна)
try:
    from app.services.rag_memory_service import get_rag_context, add_to_rag
    _HAS_RAG = True
except ImportError:
    _HAS_RAG = False
    def get_rag_context(*a, **kw): return ""
    def add_to_rag(*a, **kw): return {}

logger = logging.getLogger(__name__)

_HISTORY = RunHistoryService()
_REFLECTION_ROUTES = {"code", "project"}
_MAX_HISTORY_PAIRS = 10


def _short(v, limit=600):
    t = str(v or ""); return t if len(t) <= limit else t[:limit] + "..."

def _tl(timeline, step, title, status, detail):
    timeline.append({"step": step, "title": title, "status": status, "detail": detail})


def _apply_identity_guard(user_input: str, answer_text: str, timeline: list[dict[str, Any]]):
    guard = guard_identity_response(user_input, answer_text, persona_name="Elira")
    if guard.get("changed"):
        _tl(timeline, "identity_guard", "Идентичность Elira", "done", guard.get("reason", "identity_rewrite"))
    return guard

def _apply_provenance_guard(user_input: str, answer_text: str, timeline: list[dict[str, Any]]):
    guard = guard_provenance_response(user_input, answer_text)
    if guard.get("changed"):
        _tl(timeline, "provenance_guard", "Ответ без служебных источников", "done", guard.get("reason", "source_hidden"))
    return guard


def _resolve_agent_os_source_id(agent_id: str | None, registry_agent: dict[str, Any] | None) -> str:
    """Facade -- delegates to application.chat.agent_os."""
    return _app_resolve_agent_os_source_id(agent_id, registry_agent)


def _emit_agent_os_event(*, event_type: str, source_agent_id: str = "", payload: dict[str, Any] | None = None) -> None:
    """Facade -- delegates to application.chat.agent_os."""
    _app_emit_agent_os_event(
        event_type=event_type,
        source_agent_id=source_agent_id,
        payload=payload,
    )


def _record_agent_os_monitoring(
    *,
    agent_id: str,
    run_id: str,
    route: str,
    model_name: str,
    ok: bool,
    duration_ms: int,
    streaming: bool,
    num_ctx: int,
    selected_tools: list[str] | None,
) -> None:
    """Facade -- delegates to application.chat.agent_os."""
    _app_record_agent_os_monitoring(
        agent_id=agent_id,
        run_id=run_id,
        route=route,
        model_name=model_name,
        ok=ok,
        duration_ms=duration_ms,
        streaming=streaming,
        num_ctx=num_ctx,
        selected_tools=selected_tools,
    )


_DIRECT_PERSONAL_MEMORY_RE = re.compile(
    r"(?iu)^\s*(?:как\s+меня\s+зовут|ты\s+знаешь\s+как\s+меня\s+зовут|what\s+is\s+my\s+name|do\s+you\s+know\s+my\s+name)\s*\??\s*$"
)


def _is_direct_personal_memory_query(user_input: str) -> bool:
    return bool(_DIRECT_PERSONAL_MEMORY_RE.search(user_input or ""))


def _should_recall_memory_context(user_input: str, route: str, temporal: dict[str, Any] | None) -> bool:
    temporal = temporal or {}
    if is_memory_command(user_input):
        return False
    if route == "research" and temporal.get("mode") == "hard" and temporal.get("freshness_sensitive"):
        return False
    return True


def _get_memory_recall_limits(user_input: str) -> tuple[int, int]:
    if _is_direct_personal_memory_query(user_input):
        return (1, 0)
    return (5, 3)


def _trim_history(h, max_pairs=_MAX_HISTORY_PAIRS):
    """Умная обрезка истории: оставляем первое сообщение (контекст) + последние N пар."""
    if not h: return []
    limit = max_pairs * 2
    if len(h) <= limit:
        return list(h)
    # Всегда сохраняем первые 2 сообщения (начальный контекст разговора)
    # + последние (limit - 2) сообщений
    first_pair = list(h[:2])
    recent = list(h[-(limit - 2):])
    return first_pair + recent



_EXEC_TRIGGERS = ["запусти", "посчитай", "вычисли", "выполни", "рассчитай", "run", "execute", "calculate", "compute"]


def _maybe_auto_exec_python(user_input, answer, timeline, enabled: bool = True):
    """Если пользователь просил выполнить и ответ содержит Python — запускаем."""
    if not enabled:
        return answer
    ql = user_input.lower()
    if not any(t in ql for t in _EXEC_TRIGGERS):
        return answer
    import re as _re
    match = _re.search(r"```python\n([\s\S]*?)```", answer)
    if not match:
        return answer
    code = match.group(1).strip()
    if not code or len(code) < 10:
        return answer
    try:
        from app.services.python_runner import execute_python
        result = execute_python(code)
        _tl(timeline, "auto_exec", "Python exec", "done" if result.get("ok") else "error", "")
        parts = ["\n\n**Результат выполнения:**"]
        if result.get("ok"):
            if result.get("stdout"):
                parts.append("```\n" + result["stdout"].strip() + "\n```")
            if result.get("locals"):
                vars_str = ", ".join(f"{k}={v}" for k, v in result["locals"].items())
                parts.append(f"Переменные: `{vars_str}`")
            if not result.get("stdout") and not result.get("locals"):
                parts.append("✓ Код выполнен без вывода")
        else:
            parts.append(f"❌ Ошибка: `{result.get('error', 'Unknown')}`")
        return answer + "\n".join(parts)
    except Exception:
        return answer


# ═══════════════════════════════════════════════════════════════
# POST-ГЕНЕРАЦИЯ ФАЙЛОВ: LLM написал ответ → сохраняем в Word/Excel
# ═══════════════════════════════════════════════════════════════


# Re-export file trigger constants from extracted module
from app.application.chat.auto_skills import (  # noqa: E402
    _FILE_TRIGGERS_WORD,
    _FILE_TRIGGERS_EXCEL,
)


def _maybe_generate_files(user_input: str, llm_answer: str, enabled: bool = True) -> str:
    """Facade -- delegates to application.chat.auto_skills."""
    from app.application.chat.auto_skills import maybe_generate_files
    return maybe_generate_files(user_input, llm_answer, enabled=enabled)


def _run_auto_skills(user_input: str, disabled: set | None = None) -> str:
    """Facade -- delegates to application.chat.auto_skills."""
    from app.application.chat.auto_skills import run_auto_skills
    return run_auto_skills(user_input, disabled=disabled)


def _compose_human_style_rules(temporal: dict[str, Any] | None) -> str:
    temporal = temporal or {}
    mode = temporal.get("mode", "none")
    freshness_sensitive = bool(temporal.get("freshness_sensitive"))
    years = ", ".join(str(year) for year in temporal.get("years", [])) or "none"
    reasoning_depth = temporal.get("reasoning_depth", "none")
    return (
        "\n\nFINAL ANSWER RULES:\n"
        "1. Answer naturally, like a thoughtful human assistant, not like a search engine dump.\n"
        "2. If web data is available, use it as working evidence but do not inject links unless the user asks for them.\n"
        "3. Never expose raw memory markers, RAG labels, hidden context, or technical source notes.\n"
        "4. If freshness is uncertain, say so plainly.\n"
        "5. If the user asks about sources, explain them naturally without technical jargon.\n"
        "6. If the answer contains steps, events, comparisons, or multiple subtopics, format them as vertical Markdown lists or short sections.\n"
        "7. For long answers, start with a short takeaway and then break details into bullets or numbered steps.\n"
        "8. Avoid dense text walls when the same content can be shown more clearly with headings, bullets, numbering, or short paragraphs.\n"
        "9. Use valid Markdown when helpful: `-` for lists, `1.` for steps, and `**...**` for key facts.\n"
        f"10. Temporal mode: {mode}; explicit years: {years}; reasoning depth: {reasoning_depth}; freshness sensitive: {freshness_sensitive}."
    )




def _wants_explicit_datetime_answer(user_input: str) -> bool:
    q = (user_input or "").strip().lower()
    if not q:
        return False

    explicit_phrases = (
        "какая сегодня дата",
        "сегодня какая дата",
        "какое сегодня число",
        "сегодня какое число",
        "какой сегодня день",
        "какой сегодня день недели",
        "какая дата сегодня",
        "который час",
        "сколько времени",
        "сколько сейчас времени",
        "какое сейчас время",
        "текущее время",
        "текущая дата",
        "what date is it",
        "what time is it",
        "current date",
        "current time",
        "today's date",
    )
    if any(phrase in q for phrase in explicit_phrases):
        return True

    explicit_patterns = (
        r"\bкотор(?:ый|ое)\s+час\b",
        r"\bсколько\s+(?:сейчас\s+)?времени\b",
        r"\bкакая\s+(?:сегодня\s+)?дата\b",
        r"\bкакое\s+(?:сегодня\s+)?число\b",
        r"\bкакой\s+(?:сегодня\s+)?день(?:\s+недели)?\b",
        r"\bwhat\s+date\b",
        r"\bwhat\s+time\b",
    )
    return any(re.search(pattern, q, flags=re.IGNORECASE) for pattern in explicit_patterns)


def _build_runtime_datetime_context(user_input: str) -> str:
    from datetime import datetime

    days_ru = {
        "Monday": "понедельник",
        "Tuesday": "вторник",
        "Wednesday": "среда",
        "Thursday": "четверг",
        "Friday": "пятница",
        "Saturday": "суббота",
        "Sunday": "воскресенье",
    }
    now = datetime.now()
    day_name = days_ru.get(now.strftime("%A"), now.strftime("%A"))
    runtime_stamp = f"{now.strftime('%d.%m.%Y, %H:%M')}, {day_name}"

    if _wants_explicit_datetime_answer(user_input):
        return (
            "ВНУТРЕННИЙ RUNTIME-КОНТЕКСТ:\n"
            f"- Текущая локальная дата и время: {runtime_stamp}\n"
            "- Пользователь прямо спросил о дате или времени. Ответь естественно и используй эти данные точно.\n"
            "- Не добавляй лишние технические пояснения."
        )

    return (
        "ВНУТРЕННИЙ RUNTIME-КОНТЕКСТ:\n"
        f"- Текущая локальная дата и время: {runtime_stamp}\n"
        "- Ты всегда знаешь текущие дату и время внутренне.\n"
        "- НЕ упоминай дату, время, день недели или фразы вида "
        "\"Сегодня ... и сейчас ...\" в обычном ответе, если пользователь прямо об этом не спросил.\n"
        "- Используй эти данные молча только когда они действительно нужны для логики ответа."
    )


def _build_prompt(user_input, context_bundle, mode="default", disabled_skills: set | None = None):
    runtime_context = _build_runtime_datetime_context(user_input)

    skill_results = _run_auto_skills(user_input, disabled=disabled_skills or set())

    _pending_attachments.clear()
    if skill_results:
        clean_parts = []
        for line in skill_results.split("\n\n"):
            if line.startswith("IMAGE_GENERATED:"):
                p = line.split(":", 4)
                if len(p) >= 4:
                    _pending_attachments.append({
                        "type": "image",
                        "view_url": p[1] + ":" + p[2] if "http" in p[1] else p[1],
                        "filename": p[2] if "http" not in p[1] else p[3],
                        "prompt": p[-1],
                    })
            elif line.startswith("FILE_GENERATED:"):
                p = line.split(":", 4)
                if len(p) >= 4:
                    _pending_attachments.append({
                        "type": "file",
                        "file_type": p[1],
                        "download_url": p[2] + ":" + p[3] if "http" in p[2] else p[2],
                        "filename": p[3] if "http" not in p[2] else p[4] if len(p) > 4 else p[3],
                    })
            elif line.startswith("SKILL_HINT:"):
                clean_parts.append(line)
            elif line.startswith("SKILL_ERROR:"):
                error_msg = line[len("SKILL_ERROR:"):]
                _pending_attachments.append({"type": "error", "message": error_msg})
            else:
                clean_parts.append(line)
        skill_results = "\n\n".join(clean_parts)

    if skill_results:
        context_bundle = (context_bundle + "\n\n" + skill_results) if context_bundle.strip() else skill_results

    if not context_bundle.strip():
        return f"{runtime_context}\n\nВопрос пользователя: {user_input}"

    return (
        f"{runtime_context}\n\n"
        "Вот данные из интернета и других источников:\n\n"
        + context_bundle
        + "\n\n---\n\n"
        "Вопрос пользователя: " + user_input + "\n\n"
        "ПРАВИЛА ОТВЕТА:\n"
        "1. Обязательно используй данные выше для ответа.\n"
        "2. Если есть содержимое веб-страниц или свежие новости, опирайся на них как на главный источник.\n"
        "3. Приводи конкретные факты, даты и цифры, но без служебных маркеров и внутреннего контекста.\n"
        "4. Не вставляй URL и список источников, если пользователь прямо не попросил ссылки или источники.\n"
        "5. Если свежесть данных под вопросом, честно скажи об этом простыми словами.\n"
        "6. Не говори, что данных нет, если они есть выше.\n"
        "7. Не упоминай текущую дату или время, если пользователь прямо об этом не спросил. "
        "Если спросил — отвечай точно и естественно."
    )


_pending_attachments: list[dict] = []


def _get_and_clear_attachments() -> str:
    """Возвращает markdown-блок с картинками/файлами/ошибками и очищает очередь."""
    if not _pending_attachments:
        return ""
    api_base = ""
    parts = []
    for att in _pending_attachments:
        if att["type"] == "image":
            url = att["view_url"] if att["view_url"].startswith("http") else f"{api_base}{att['view_url']}"
            dl = f"{api_base}/api/skills/download/{att.get('filename', '')}"
            parts.append(f"\n\n🎨 **Сгенерировано:**\n\n![{att.get('prompt','')}]({url})\n\n📥 [Скачать]({dl})")
        elif att["type"] == "file":
            dl = att["download_url"] if att["download_url"].startswith("http") else f"{api_base}{att['download_url']}"
            icon = {"word": "📄", "zip": "📦", "convert": "🔄", "excel": "📊"}.get(att.get("file_type", ""), "📎")
            parts.append(f"\n\n{icon} **Файл создан:** [{att.get('filename', '')}]({dl})")
        elif att["type"] == "error":
            parts.append(f"\n\n⚠️ {att.get('message', 'Ошибка скилла')}")
    _pending_attachments.clear()
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# ГЛУБОКИЙ ВЕБ-ПОИСК: поиск → заход на сайты → извлечение текста

_clean_query = _infra_clean_query
_is_strict_web_only_query = _infra_is_strict_web_only_query
_get_web_search_result = _infra_get_web_search_result
_build_single_web_subquery_context = _infra_build_single_web_subquery_context
_do_web_search_legacy = partial(_infra_do_web_search_legacy, tl=_tl)
_do_temporal_web_search_legacy = partial(_infra_do_temporal_web_search_legacy, tl=_tl)
_do_web_search = partial(_infra_do_web_search, tl=_tl)
_do_temporal_web_search = partial(_infra_do_temporal_web_search, tl=_tl)




# ═══════════════════════════════════════════════════════════════
def _strip_frontend_project_context(user_input: str) -> str:
    return build_strip_frontend_project_context(user_input)


def _collect_context(**kwargs):
    return build_chat_context(
        run_tool_func=run_tool,
        append_timeline=_tl,
        temporal_web_search_func=_do_temporal_web_search,
        **kwargs,
    )

# run_agent
# ═══════════════════════════════════════════════════════════════

def run_agent(*, model_name, profile_name, user_input, session_id=None, agent_id=None, use_memory=True, use_library=True, use_reflection=False, history=None, num_ctx=8192, use_web_search=True, use_python_exec=True, use_image_gen=True, use_file_gen=True, use_http_api=True, use_sql=True, use_screenshot=True, use_encrypt=True, use_archiver=True, use_converter=True, use_regex=True, use_translator=True, use_csv=True, use_webhook=True, use_plugins=True):
    import time as _time
    _agent_start = _time.monotonic()

    # Agent OS: если указан agent_id, загружаем определение из реестра
    _registry_agent = None
    if agent_id:
        try:
            from app.services.agent_registry import resolve_agent
            _registry_agent = resolve_agent(agent_id=agent_id)
            if _registry_agent:
                if _registry_agent.get("system_prompt"):
                    profile_name = _registry_agent.get("name_ru") or profile_name
                if _registry_agent.get("model_preference"):
                    model_name = _registry_agent["model_preference"]
        except Exception:
            pass

    _effective_agent_id = resolve_effective_agent_id(
        agent_id=agent_id,
        profile_name=profile_name,
        registry_agent=_registry_agent,
    )
    history = _trim_history(history or [])
    _disabled_skills = build_disabled_skills(
        use_web_search=use_web_search,
        use_python_exec=use_python_exec,
        use_image_gen=use_image_gen,
        use_file_gen=use_file_gen,
        use_http_api=use_http_api,
        use_sql=use_sql,
        use_screenshot=use_screenshot,
        use_encrypt=use_encrypt,
        use_archiver=use_archiver,
        use_converter=use_converter,
        use_regex=use_regex,
        use_translator=use_translator,
        use_csv=use_csv,
        use_webhook=use_webhook,
        use_plugins=use_plugins,
    )
    timeline, tool_results = [], []
    planner = PlannerV2Service()
    raw_user_input = user_input
    planner_input = _strip_frontend_project_context(user_input)
    run = _HISTORY.start_run(raw_user_input)
    _agent_os_source_id = _effective_agent_id
    _emit_agent_os_event(
        event_type="agent.run.started",
        source_agent_id=_agent_os_source_id,
        payload={
            "run_id": run["run_id"],
            "profile_name": profile_name,
            "requested_model": model_name,
            "session_id": str(session_id or ""),
            "streaming": False,
        },
    )
    try:
        chat_plan = prepare_chat_plan(
            planner_input=planner_input,
            model_name=model_name,
            plan_runner=planner.plan,
            use_memory=use_memory,
            use_library=use_library,
            use_web_search=use_web_search,
            is_memory_command_func=is_memory_command,
            pick_model_for_route_func=pick_model_for_route,
        )
        plan = chat_plan.plan
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = chat_plan.route
        temporal = chat_plan.temporal
        web_plan = chat_plan.web_plan
        effective_model = chat_plan.effective_model
        selected = chat_plan.selected_tools

        # Умная память: извлекаем факты из сообщения
        try:
            saved = extract_and_save(planner_input)
            if saved:
                _tl(timeline, "memory_save", "Память", "done", "Сохранено: " + str(len(saved)))
        except Exception:
            pass

        preflight_or_raise(
            agent_id=_effective_agent_id,
            num_ctx=num_ctx,
            selected_tools=selected,
            run_id=run["run_id"],
            route=route,
            streaming=False,
        )

        ctx = _collect_context(profile_name=profile_name, user_input=planner_input, tools=selected, tool_results=tool_results, timeline=timeline, use_reflection=use_reflection, temporal=temporal, web_plan=web_plan)

        # Умная память + RAG: добавляем релевантные воспоминания только когда это реально нужно
        if _should_recall_memory_context(planner_input, route, temporal):
            try:
                mem_limit, rag_limit = _get_memory_recall_limits(planner_input)
                mem_ctx = get_relevant_context(planner_input, max_items=mem_limit)
                if _HAS_RAG and rag_limit > 0:
                    rag_ctx = get_rag_context(planner_input, max_items=rag_limit)
                    if rag_ctx:
                        mem_ctx = (mem_ctx + "\n\n" + rag_ctx) if mem_ctx else rag_ctx
                if mem_ctx:
                    ctx = mem_ctx + "\n\n" + ctx if ctx else mem_ctx
                    _tl(timeline, "memory_recall", "Память", "done", "Найдены релевантные заметки")
            except Exception:
                pass

        prompt = _build_prompt(raw_user_input, ctx, disabled_skills=_disabled_skills) + _compose_human_style_rules(temporal)
        task_context = build_task_context(route, selected)
        draft = run_chat(model_name=effective_model, profile_name=profile_name, user_input=prompt, history=history, num_ctx=num_ctx, task_context=task_context)
        if not draft.get("ok"):
            raise RuntimeError("; ".join(draft.get("warnings", [])) or "LLM failed")
        answer = draft.get("answer", "")

        # Reflection: для code/project ИЛИ если пользователь включил скилл
        has_generated_files = any(a["type"] in ("image", "file") for a in _pending_attachments)
        should_reflect = (route in _REFLECTION_ROUTES) or use_reflection
        if should_reflect and answer.strip() and not has_generated_files:
            ref = run_reflection_loop(model_name=effective_model, profile_name=profile_name, user_input=raw_user_input, draft_text=answer, review_text="Улучши.", context=ctx)
            answer = ref.get("answer") or answer

        # Добавляем вложения (картинки, файлы)
        attachments = _get_and_clear_attachments()
        if attachments:
            answer += attachments

        # POST-генерация: Word/Excel из ответа LLM
        post_files = _maybe_generate_files(raw_user_input, answer, enabled=use_file_gen)
        if post_files:
            answer += post_files

        identity_guard = _apply_identity_guard(raw_user_input, answer, timeline)
        answer = identity_guard.get("text", answer)
        provenance_guard = _apply_provenance_guard(raw_user_input, answer, timeline)
        answer = provenance_guard.get("text", answer)

        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
        meta = _app_finalize_chat_success(
            history_service=_HISTORY,
            run_id=run["run_id"],
            session_id=str(session_id or ""),
            profile_name=profile_name,
            model_name=effective_model,
            route=route,
            user_input=raw_user_input,
            answer_text=answer,
            tools=selected,
            temporal=temporal,
            web_plan=web_plan,
            identity_guard=identity_guard,
            provenance_guard=provenance_guard,
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            agent_id=_effective_agent_id,
            source_agent_id=_agent_os_source_id,
            selected_tools=selected,
        )
        result = {
            "ok": True,
            "answer": answer,
            "timeline": timeline,
            "tool_results": tool_results,
            "meta": meta,
        }

        # Agent OS: записываем запуск в реестр
        if agent_id or _registry_agent:
            try:
                from app.services.agent_registry import record_agent_run
                record_agent_run({
                    "agent_id": agent_id or (_registry_agent or {}).get("id", ""),
                    "run_id": run["run_id"],
                    "input_summary": raw_user_input[:500],
                    "output_summary": answer[:500],
                    "ok": True,
                    "route": route,
                    "model_used": effective_model,
                    "duration_ms": _duration_ms,
                })
            except Exception:
                pass
        return result
    except SandboxPolicyError as exc:
        err = {
            "ok": False,
            "answer": "",
            "timeline": timeline + [{"step": "sandbox", "title": "Sandbox", "status": "error", "detail": str(exc)}],
            "tool_results": tool_results,
            "meta": {
                "error": str(exc),
                "run_id": run["run_id"],
                "sandbox_reason": exc.reason,
                "sandbox_details": exc.details,
            },
        }
        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
        _app_finalize_chat_failure(
            history_service=_HISTORY,
            run_id=run["run_id"],
            profile_name=profile_name,
            model_name=locals().get("effective_model", model_name),
            route=locals().get("route", ""),
            error_text=str(exc),
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            agent_id=_effective_agent_id,
            source_agent_id=_agent_os_source_id,
            session_id=str(session_id or ""),
            selected_tools=locals().get("selected", []),
            history_payload=err,
        )
        return err
    except Exception as exc:
        err = {"ok": False, "answer": "", "timeline": timeline + [{"step": "error", "title": "Ошибка", "status": "error", "detail": str(exc)}], "tool_results": tool_results, "meta": {"error": str(exc), "run_id": run["run_id"]}}
        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)

        _app_finalize_chat_failure(
            history_service=_HISTORY,
            run_id=run["run_id"],
            profile_name=profile_name,
            model_name=locals().get("effective_model", model_name),
            route=locals().get("route", ""),
            error_text=str(exc),
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            agent_id=_effective_agent_id,
            source_agent_id=_agent_os_source_id,
            session_id=str(session_id or ""),
            selected_tools=locals().get("selected", []),
            history_payload=err,
        )
        if agent_id or _registry_agent:
            try:
                from app.services.agent_registry import record_agent_run
                record_agent_run({
                    "agent_id": agent_id or (_registry_agent or {}).get("id", ""),
                    "run_id": run["run_id"],
                    "input_summary": raw_user_input[:500] if 'raw_user_input' in dir() else user_input[:500],
                    "output_summary": str(exc)[:500],
                    "ok": False,
                    "route": "",
                    "model_used": model_name,
                    "duration_ms": _duration_ms,
                })
            except Exception:
                pass

        return err


# ═══════════════════════════════════════════════════════════════
# run_agent_stream
# ═══════════════════════════════════════════════════════════════

def run_agent_stream(*, model_name, profile_name, user_input, session_id=None, use_memory=True, use_library=True, use_reflection=False, history=None, num_ctx=8192, use_web_search=True, use_python_exec=True, use_image_gen=True, use_file_gen=True, use_http_api=True, use_sql=True, use_screenshot=True, use_encrypt=True, use_archiver=True, use_converter=True, use_regex=True, use_translator=True, use_csv=True, use_webhook=True, use_plugins=True):
    import time as _time
    _agent_start = _time.monotonic()
    _effective_agent_id = resolve_effective_agent_id(profile_name=profile_name)
    history = _trim_history(history or [])
    _disabled_skills = build_disabled_skills(
        use_web_search=use_web_search,
        use_python_exec=use_python_exec,
        use_image_gen=use_image_gen,
        use_file_gen=use_file_gen,
        use_http_api=use_http_api,
        use_sql=use_sql,
        use_screenshot=use_screenshot,
        use_encrypt=use_encrypt,
        use_archiver=use_archiver,
        use_converter=use_converter,
        use_regex=use_regex,
        use_translator=use_translator,
        use_csv=use_csv,
        use_webhook=use_webhook,
        use_plugins=use_plugins,
    )
    timeline, tool_results = [], []
    planner = PlannerV2Service()
    raw_user_input = user_input
    planner_input = _strip_frontend_project_context(user_input)
    run = _HISTORY.start_run(raw_user_input)
    _emit_agent_os_event(
        event_type="agent.run.started",
        source_agent_id=_effective_agent_id,
        payload={
            "run_id": run["run_id"],
            "profile_name": profile_name,
            "requested_model": model_name,
            "session_id": str(session_id or ""),
            "streaming": True,
        },
    )
    try:
        yield {"token": "", "done": False, "phase": "planning", "message": "Думаю..."}

        chat_plan = prepare_chat_plan(
            planner_input=planner_input,
            model_name=model_name,
            plan_runner=planner.plan,
            use_memory=use_memory,
            use_library=use_library,
            use_web_search=use_web_search,
            is_memory_command_func=is_memory_command,
            pick_model_for_route_func=pick_model_for_route,
        )
        plan = chat_plan.plan
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = chat_plan.route
        temporal = chat_plan.temporal
        web_plan = chat_plan.web_plan
        selected = chat_plan.selected_tools
        effective_model = chat_plan.effective_model
        preflight_or_raise(
            agent_id=_effective_agent_id,
            num_ctx=num_ctx,
            selected_tools=selected,
            run_id=run["run_id"],
            route=route,
            streaming=True,
        )
        if effective_model != model_name:
            _tl(timeline, "auto_model", "Авто-модель", "ok", f"{model_name} → {effective_model} (route={route})")

        # ═══ КЭШИРОВАНИЕ ═══
        if should_cache(planner_input, route) and not history:
            cached = get_cached(planner_input, effective_model, profile_name)
            if cached:
                _tl(timeline, "cache_hit", "Кэш", "ok", "Ответ из кэша")
                identity_guard = _apply_identity_guard(raw_user_input, cached, timeline)
                cached = identity_guard.get("text", cached)
                provenance_guard = _apply_provenance_guard(raw_user_input, cached, timeline)
                cached = provenance_guard.get("text", cached)
                _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
                done_event = _app_finalize_stream_success(
                    history_service=_HISTORY,
                    run_id=run["run_id"],
                    session_id=str(session_id or ""),
                    profile_name=profile_name,
                    model_name=effective_model,
                    route=route,
                    user_input=raw_user_input,
                    full_text=cached,
                    tools=[],
                    temporal=temporal,
                    web_plan=web_plan,
                    identity_guard=identity_guard,
                    provenance_guard=provenance_guard,
                    duration_ms=_duration_ms,
                    num_ctx=num_ctx,
                    agent_id=_effective_agent_id,
                    source_agent_id=_effective_agent_id,
                    timeline=timeline,
                    selected_tools=selected,
                    cached=True,
                )
                # Стримим кэшированный ответ по токенам (выглядит естественно)
                for token_event in iter_text_stream_events(cached):
                    yield token_event
                yield done_event
                return

        # Умная память: извлекаем факты
        try:
            extract_and_save(planner_input)
        except Exception:
            pass

        if "web_search" in selected:
            yield {"token": "", "done": False, "phase": "searching", "message": "Ищу..."}
        elif selected:
            yield {"token": "", "done": False, "phase": "tools", "message": "Собираю контекст..."}

        ctx = _collect_context(profile_name=profile_name, user_input=planner_input, tools=selected, tool_results=tool_results, timeline=timeline, use_reflection=use_reflection, temporal=temporal, web_plan=web_plan)

        # Умная память + RAG
        mem_count = 0
        if _should_recall_memory_context(planner_input, route, temporal):
            try:
                mem_limit, rag_limit = _get_memory_recall_limits(planner_input)
                mem_ctx = get_relevant_context(planner_input, max_items=mem_limit)
                if mem_ctx:
                    mem_count = mem_ctx.count("\n- ")
                if _HAS_RAG and rag_limit > 0:
                    rag_ctx = get_rag_context(planner_input, max_items=rag_limit)
                    if rag_ctx:
                        mem_ctx = (mem_ctx + "\n\n" + rag_ctx) if mem_ctx else rag_ctx
                if mem_ctx:
                    ctx = mem_ctx + "\n\n" + ctx if ctx else mem_ctx
            except Exception:
                pass

        yield {"token": "", "done": False, "phase": "thinking", "message": "Пишу ответ..."}

        prompt = _build_prompt(raw_user_input, ctx, disabled_skills=_disabled_skills) + _compose_human_style_rules(temporal)
        full_text = ""
        task_context = build_task_context(route, selected)
        for token in run_chat_stream(model_name=effective_model, profile_name=profile_name, user_input=prompt, history=history, num_ctx=num_ctx, task_context=task_context):
            full_text += token
            yield {"token": token, "done": False}

        # Добавляем вложения (картинки, файлы) — быстрая операция
        attachments = _get_and_clear_attachments()
        if attachments:
            full_text += attachments

        # Проверяем нужны ли тяжёлые пост-операции
        has_generated_files = any(a["type"] in ("image", "file") for a in _pending_attachments)
        should_reflect = (route in _REFLECTION_ROUTES) or use_reflection
        ql_check = raw_user_input.lower()
        needs_file_gen = any(t in ql_check for t in _FILE_TRIGGERS_WORD + _FILE_TRIGGERS_EXCEL)

        # Если нет тяжёлых операций — отправляем done СРАЗУ (быстрый путь)
        if not should_reflect and not needs_file_gen:
            # Авто-выполнение Python (лёгкое, только если есть код)
            try:
                full_text = _maybe_auto_exec_python(raw_user_input, full_text, timeline, enabled=use_python_exec)
            except Exception:
                pass
            post_files = _maybe_generate_files(raw_user_input, full_text, enabled=use_file_gen)
            if post_files:
                full_text += post_files
            identity_guard = _apply_identity_guard(raw_user_input, full_text, timeline)
            guarded_text = identity_guard.get("text", full_text)
            provenance_guard = _apply_provenance_guard(raw_user_input, guarded_text, timeline)
            guarded_text = provenance_guard.get("text", guarded_text)
            if guarded_text != full_text:
                full_text = guarded_text
                yield {"token": "", "done": False, "phase": "reflection_replace", "full_text": full_text}
            if should_cache(planner_input, route) and full_text.strip():
                try:
                    set_cached(planner_input, effective_model, profile_name, full_text)
                except Exception:
                    pass
            _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
            yield _app_finalize_stream_success(
                history_service=_HISTORY,
                run_id=run["run_id"],
                session_id=str(session_id or ""),
                profile_name=profile_name,
                model_name=effective_model,
                route=route,
                user_input=raw_user_input,
                full_text=full_text,
                tools=selected,
                temporal=temporal,
                web_plan=web_plan,
                identity_guard=identity_guard,
                provenance_guard=provenance_guard,
                duration_ms=_duration_ms,
                num_ctx=num_ctx,
                agent_id=_effective_agent_id,
                source_agent_id=_effective_agent_id,
                timeline=timeline,
                selected_tools=selected,
            )
        else:
            # Тяжёлый путь — reflection и/или генерация файлов
            if should_reflect and full_text.strip() and not has_generated_files:
                yield {"token": "", "done": False, "phase": "reflecting", "message": "Проверяю..."}
                try:
                    ref = run_reflection_loop(model_name=effective_model, profile_name=profile_name, user_input=raw_user_input, draft_text=full_text, review_text="Улучши.", context=ctx)
                    refined = ref.get("answer", "")
                    if refined and refined != full_text:
                        full_text = refined
                        yield {"token": "", "done": False, "phase": "reflection_replace", "full_text": refined}
                except Exception:
                    pass

            try:
                full_text = _maybe_auto_exec_python(raw_user_input, full_text, timeline, enabled=use_python_exec)
            except Exception:
                pass

            if needs_file_gen:
                yield {"token": "", "done": False, "phase": "generating_file", "message": "Готовлю файл..."}
            post_files = _maybe_generate_files(raw_user_input, full_text, enabled=use_file_gen)
            if post_files:
                full_text += post_files

            identity_guard = _apply_identity_guard(raw_user_input, full_text, timeline)
            guarded_text = identity_guard.get("text", full_text)
            provenance_guard = _apply_provenance_guard(raw_user_input, guarded_text, timeline)
            guarded_text = provenance_guard.get("text", guarded_text)
            if guarded_text != full_text:
                full_text = guarded_text
                yield {"token": "", "done": False, "phase": "reflection_replace", "full_text": full_text}

            # Кэшируем после всех пост-обработок
            if should_cache(planner_input, route) and full_text.strip():
                try:
                    set_cached(planner_input, effective_model, profile_name, full_text)
                except Exception:
                    pass

            _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
            yield _app_finalize_stream_success(
                history_service=_HISTORY,
                run_id=run["run_id"],
                session_id=str(session_id or ""),
                profile_name=profile_name,
                model_name=effective_model,
                route=route,
                user_input=raw_user_input,
                full_text=full_text,
                tools=selected,
                temporal=temporal,
                web_plan=web_plan,
                identity_guard=identity_guard,
                provenance_guard=provenance_guard,
                duration_ms=_duration_ms,
                num_ctx=num_ctx,
                agent_id=_effective_agent_id,
                source_agent_id=_effective_agent_id,
                timeline=timeline,
                selected_tools=selected,
            )
    except Exception as exc:
        _app_finalize_chat_failure(
            history_service=_HISTORY,
            run_id=run["run_id"],
            profile_name=profile_name,
            model_name=locals().get("effective_model", model_name),
            route=locals().get("route", ""),
            error_text=str(exc),
            duration_ms=int((_time.monotonic() - _agent_start) * 1000),
            streaming=True,
            num_ctx=num_ctx,
            agent_id=_effective_agent_id,
            source_agent_id=_effective_agent_id,
            session_id=str(session_id or ""),
            selected_tools=locals().get("selected", []),
            history_payload={"ok": False, "error": str(exc)},
        )
        yield {"token": "", "done": True, "error": str(exc), "full_text": ""}
