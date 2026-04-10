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
from app.application.chat.stream_service import (
    build_chat_meta,
    build_stream_done_event,
    iter_text_stream_events,
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
from app.services.persona_service import observe_dialogue
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

_QUERY_NOISE = [
    r"^(дай|дай мне|покажи|скажи|расскажи|найди|покажи мне)\s+",
    r"\s+(пожалуйста|плиз|please)$",
]


def _clean_query(query):
    """Очищает и УЛУЧШАЕТ запрос для поисковика."""
    from datetime import datetime
    q = query.strip()
    for p in _QUERY_NOISE:
        q = re.sub(p, "", q, flags=re.IGNORECASE).strip()

    ql = q.lower()

    # Определяем тип запроса
    is_news = any(w in ql for w in ["новости", "новость", "события", "произошло", "случилось", "происшеств"])
    is_price = any(w in ql for w in ["курс", "цена", "стоимость"])
    is_weather = "погода" in ql

    # Добавляем год если нет
    temporal = detect_temporal_intent(q)
    if (is_news or is_price or is_weather) and not temporal.get("years"):
        q += " " + str(datetime.now().year)

    # Раскрываем короткие даты: "19.03" → "19 марта 2025"
    date_match = re.search(r"(\d{1,2})\.(\d{2})(?:\.\d{2,4})?", q)
    if date_match and is_news:
        day = date_match.group(1)
        month_num = int(date_match.group(2))
        months = {1:"января",2:"февраля",3:"марта",4:"апреля",5:"мая",6:"июня",
                  7:"июля",8:"августа",9:"сентября",10:"октября",11:"ноября",12:"декабря"}
        month_name = months.get(month_num, "")
        if month_name:
            q = re.sub(r"\d{1,2}\.\d{2}(?:\.\d{2,4})?", f"{day} {month_name}", q)

    # Добавляем "Казахстан" для новостей без указания страны
    if is_news and not any(w in ql for w in ["россия", "украина", "сша", "мир", "казахстан", "кз"]):
        # Если есть город КЗ — добавляем "Казахстан"
        kz_cities = ["алматы", "астана", "шымкент", "караганд", "актау", "атырау", "павлодар", "семей", "тараз"]
        if any(c in ql for c in kz_cities):
            q += " Казахстан"

    return q or query


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
    return str(agent_id or (registry_agent or {}).get("id") or "")


def _emit_agent_os_event(*, event_type: str, source_agent_id: str = "", payload: dict[str, Any] | None = None) -> None:
    try:
        from app.services.event_bus import emit_event

        emit_event(
            event_type=event_type,
            source_agent_id=source_agent_id,
            payload=payload or {},
        )
    except Exception:
        logger.debug("event_bus_emit_failed", exc_info=True)


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
    try:
        record_agent_run_metric(
            agent_id=agent_id,
            run_id=run_id,
            route=route,
            model_name=model_name,
            ok=ok,
            duration_ms=duration_ms,
            streaming=streaming,
            num_ctx=int(num_ctx or 0),
            tools=list(selected_tools or []),
        )
    except Exception:
        logger.debug("agent_monitor_record_failed", exc_info=True)


def _compose_human_style_rules(temporal: dict[str, Any] | None) -> str:
    temporal = temporal or {}
    mode = temporal.get("mode", "none")
    freshness_sensitive = bool(temporal.get("freshness_sensitive"))
    years = ", ".join(str(year) for year in temporal.get("years", [])) or "нет"
    reasoning_depth = temporal.get("reasoning_depth", "none")
    return (
        "\n\nПРАВИЛА ФИНАЛЬНОГО ОТВЕТА:\n"
        "1. Отвечай естественно, как живой человек, а не как поисковая система.\n"
        "2. Если выше есть веб-данные, используй их как рабочую базу, но не вставляй ссылки без прямой просьбы пользователя.\n"
        "3. Не показывай служебные маркеры, внутренние заметки, память, RAG, hidden context или raw tags.\n"
        "4. Если свежесть данных не подтверждена, скажи об этом простыми словами.\n"
        "5. Если пользователь спросит об источниках, тогда объясни их естественно и без технических терминов.\n"
        "6. Р•СЃР»Рё РІ РѕС‚РІРµС‚Рµ РµСЃС‚СЊ С€Р°РіРё, РїРµСЂРµС‡РёСЃР»РµРЅРёРµ, РЅРµСЃРєРѕР»СЊРєРѕ СЃРѕР±С‹С‚РёР№, СЃСЂР°РІРЅРµРЅРёРµ РёР»Рё РЅРµСЃРєРѕР»СЊРєРѕ РїРѕРґС‚РµРј, РѕС„РѕСЂРјР»СЏР№ РёС… РІ РІРёРґРµ Markdown-СЃРїРёСЃРєР° РёР»Рё РєРѕСЂРѕС‚РєРёС… СЃРµРєС†РёР№.\n"
        "7. Р”Р»РёРЅРЅС‹Р№ РѕС‚РІРµС‚ РЅР°С‡РёРЅР°Р№ СЃ РєРѕСЂРѕС‚РєРѕРіРѕ РІС‹РІРѕРґР° РёР»Рё СЃР°РјРѕРіРѕ РІР°Р¶РЅРѕРіРѕ С„Р°РєС‚Р°, Р° РїРѕС‚РѕРј СЂР°СЃРєР»Р°РґС‹РІР°Р№ РґРµС‚Р°Р»Рё РїРѕ РїСѓРЅРєС‚Р°Рј.\n"
        "8. РќРµ РІС‹РґР°РІР°Р№ РґР»РёРЅРЅС‹Рµ СЃРїР»РѕС€РЅС‹Рµ Р°Р±Р·Р°С†С‹, РµСЃР»Рё С‚РµРєСЃС‚ РјРѕР¶РЅРѕ СЃРґРµР»Р°С‚СЊ РїРѕРЅСЏС‚РЅРµРµ С‡РµСЂРµР· РїРѕРґР·Р°РіРѕР»РѕРІРєРё, bullets, РЅСѓРјРµСЂР°С†РёСЋ РёР»Рё РєРѕСЂРѕС‚РєРёРµ Р°Р±Р·Р°С†С‹.\n"
        "9. РСЃРїРѕР»СЊР·СѓР№ РІР°Р»РёРґРЅС‹Р№ Markdown: `-` РґР»СЏ СЃРїРёСЃРєРѕРІ, `1.` РґР»СЏ С€Р°РіРѕРІ, `**...**` РґР»СЏ РєР»СЋС‡РµРІС‹С… Р°РєС†РµРЅС‚РѕРІ, РєРѕСЂРѕС‚РєРёРµ Р·Р°РіРѕР»РѕРІРєРё РїСЂРё РЅРµРѕР±С…РѕРґРёРјРѕСЃС‚Рё.\n"
        f"10. Temporal mode: {mode}; explicit years: {years}; reasoning depth: {reasoning_depth}; freshness sensitive: {freshness_sensitive}."
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


def _strip_frontend_project_context_legacy(user_input: str) -> str:
    """Убирает project-context, который фронт может дописывать к запросу.

    Секцию "Файлы пользователя" не трогаем, чтобы не ломать анализ
    загруженных файлов и библиотечный контекст.
    """
    text = user_input or ""
    marker = "\n\nОткрыт проект:"
    pos = text.find(marker)
    if pos >= 0:
        return text[:pos].rstrip()
    return text


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

_FILE_TRIGGERS_WORD = ["в word", "word документ", "word файл", "docx", "в ворд",
                       "документ для скач", "сохрани в документ", "для скачки",
                       "сделай документ", "создай документ", "экспорт в word",
                       "скачать документ", "файл для скач", "сохрани как документ",
                       "создай мне документ", "сделай мне документ",
                       "создай отчёт", "создай отчет", "сделай отчёт", "сделай отчет",
                       "напиши документ", "подготовь документ", "сгенерируй документ"]
_FILE_TRIGGERS_EXCEL = ["в excel", "в эксель", "xlsx", "в таблицу", "excel файл",
                        "экспорт в excel", "сделай таблицу", "создай таблицу",
                        "excel документ", "таблицу для скач", "скачать таблицу",
                        "создай excel", "сделай excel"]


def _maybe_generate_files(user_input: str, llm_answer: str, enabled: bool = True) -> str:
    """После ответа LLM: если пользователь хотел Word/Excel — создаём файлы из ответа."""
    if not enabled:
        return ""
    import time
    ql = user_input.lower()

    extra_parts = []

    # Word
    wants_word = any(t in ql for t in _FILE_TRIGGERS_WORD)
    if wants_word and len(llm_answer) > 50:
        try:
            from app.services.skills_service import generate_word
            # Извлекаем заголовок из первой строки ответа
            lines = llm_answer.strip().split("\n")
            title = ""
            for line in lines:
                clean = line.strip().strip("#").strip("*").strip()
                if clean and len(clean) > 3:
                    title = clean[:80]
                    break
            title = title or "Документ Elira"

            # Убираем markdown-разметку для чистого текста в Word
            content = llm_answer
            result = generate_word(title, content)
            if result.get("ok"):
                fname = result.get("filename", "")
                dl = result.get("download_url", "")
                extra_parts.append(f"\n\n📄 **Word документ создан:** [{fname}]({dl})")
        except Exception as e:
            extra_parts.append(f"\n\n⚠️ Word ошибка: {e}")

    # Excel
    wants_excel = any(t in ql for t in _FILE_TRIGGERS_EXCEL)
    if wants_excel and len(llm_answer) > 30:
        try:
            from app.services.skills_service import generate_excel
            import re as _re

            # Парсим markdown таблицы из ответа LLM
            table_pattern = _re.findall(r'\|(.+)\|', llm_answer)
            if table_pattern and len(table_pattern) >= 2:
                rows = []
                headers = []
                for i, row_str in enumerate(table_pattern):
                    cells = [c.strip() for c in row_str.split("|") if c.strip()]
                    # Пропускаем разделители (---)
                    if cells and all(set(c) <= {'-', ':', ' '} for c in cells):
                        continue
                    if not headers:
                        headers = cells
                    else:
                        rows.append(cells)

                if headers and rows:
                    result = generate_excel("Данные", rows, headers)
                    if result.get("ok"):
                        fname = result.get("filename", "")
                        dl = result.get("download_url", "")
                        extra_parts.append(f"\n\n📊 **Excel файл создан:** [{fname}]({dl})")
            else:
                # Нет таблицы в ответе — создаём простой Excel из текста
                lines_data = []
                for line in llm_answer.split("\n"):
                    clean = line.strip()
                    if clean and not clean.startswith("#") and not clean.startswith("---"):
                        lines_data.append([clean])
                if lines_data:
                    result = generate_excel("Экспорт", lines_data, ["Содержимое"])
                    if result.get("ok"):
                        fname = result.get("filename", "")
                        dl = result.get("download_url", "")
                        extra_parts.append(f"\n\n📊 **Excel файл создан:** [{fname}]({dl})")
        except Exception as e:
            extra_parts.append(f"\n\n⚠️ Excel ошибка: {e}")

    return "".join(extra_parts)


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


def _run_auto_skills(user_input: str, disabled: set | None = None) -> str:
    """Авто-детект скиллов по ключевым словам. disabled — набор ID отключённых скиллов."""
    import re as _re
    disabled = disabled or set()
    ql = user_input.lower()
    parts = []
    url_match = _re.search(r"(https?://\S+)", user_input)
    API_BASE = ""  # relative URLs

    # ─── 🌐 HTTP/API ───
    if "http_api" not in disabled:
     http_triggers = ["запрос к api", "api запрос", "fetch", "http запрос", "вызови api", "get запрос", "post запрос"]
     if "http_api" not in disabled and url_match and any(t in ql for t in http_triggers + ["покажи сайт", "загрузи url", "открой ссылку"]):
        try:
            from app.services.skills_service import http_request
            method = "POST" if "post" in ql else "GET"
            result = http_request(url_match.group(1), method=method, timeout=10)
            if result.get("ok"):
                body = result.get("body", "")
                body_str = json.dumps(body, ensure_ascii=False, indent=2)[:3000] if isinstance(body, (dict, list)) else str(body)[:3000]
                parts.append(f"HTTP {method} {url_match.group(1)} → статус {result.get('status')} ({result.get('elapsed_ms')}ms):\n{body_str}")
            else:
                parts.append(f"SKILL_ERROR:🌐 HTTP ошибка: {result.get('error')}")
        except Exception as e:
            parts.append(f"SKILL_ERROR:🌐 HTTP ошибка: {e}")

    # ─── 🗄 SQL ───
    sql_triggers = ["покажи таблиц", "запрос к базе", "sql запрос", "база данных", "покажи базу", "select ", "покажи записи", "покажи данные из"]
    if "sql" not in disabled and any(t in ql for t in sql_triggers):
        try:
            from app.services.skills_service import list_databases, describe_db, run_sql
            sql_match = _re.search(r"(SELECT\s+.+)", user_input, _re.IGNORECASE)
            if sql_match:
                dbs = list_databases()
                if dbs.get("databases"):
                    result = run_sql(dbs["databases"][0]["path"], sql_match.group(1), max_rows=20)
                    if result.get("ok"):
                        parts.append(f"SQL результат ({result.get('count',0)} строк):\n{json.dumps(result.get('rows',[]), ensure_ascii=False, indent=2)[:3000]}")
            else:
                dbs = list_databases()
                if dbs.get("databases"):
                    lines = ["Доступные базы данных:"]
                    for db in dbs["databases"]:
                        desc = describe_db(db["path"])
                        for tbl, info in desc.get("tables", {}).items():
                            cols = ", ".join(c["name"] for c in info["columns"])
                            lines.append(f"  📁 {db['name']} → {tbl} ({info['rows']} строк): {cols}")
                    parts.append("\n".join(lines))
        except Exception as e:
            parts.append(f"SKILL_ERROR:🗄 SQL ошибка: {e}")

    # ─── 🖼 Скриншот ───
    screenshot_triggers = ["скриншот", "screenshot", "покажи как выглядит", "сделай снимок"]
    if "screenshot" not in disabled and url_match and any(t in ql for t in screenshot_triggers):
        try:
            from app.services.skills_service import screenshot_url
            result = screenshot_url(url_match.group(1))
            if result.get("ok"):
                parts.append(f"IMAGE_GENERATED:{result.get('view_url','')}:{result.get('filename','')}:Скриншот {result.get('title','')}")
            else:
                parts.append(f"SKILL_ERROR:🖼 Скриншот: {result.get('error')}")
        except Exception as e:
            parts.append(f"SKILL_ERROR:🖼 Скриншот: {e}")

    # ─── 🎨 Генерация картинок ───
    img_triggers = ["нарисуй", "нарисуй мне", "сгенерируй картинк", "сгенерируй изображен",
                    "создай картинк", "создай изображен", "generate image", "draw me",
                    "сделай картинк", "покажи картинк", "нарисовать"]
    if "image_gen" not in disabled and any(t in ql for t in img_triggers):
        try:
            from app.services.image_gen import generate_image
            prompt = user_input
            for t in img_triggers:
                idx = ql.find(t)
                if idx >= 0:
                    prompt = user_input[idx + len(t):].strip().strip(":").strip()
                    break
            if not prompt or len(prompt) < 3:
                prompt = user_input
            result = generate_image(prompt=prompt, width=768, height=768, steps=4)
            if result.get("ok"):
                parts.append(f"IMAGE_GENERATED:{result.get('view_url','')}:{result.get('filename','')}:{prompt}")
            else:
                parts.append(f"SKILL_ERROR:🎨 Генерация: {result.get('error')}")
        except ImportError:
            parts.append("SKILL_ERROR:🎨 Для картинок установи: pip install diffusers transformers accelerate torch sentencepiece protobuf")
        except Exception as e:
            parts.append(f"SKILL_ERROR:🎨 Генерация: {e}")

    # ─── 📝 Word/Excel: НЕ генерируем заранее — файлы создаются ПОСЛЕ ответа LLM через _maybe_generate_files ───
    # Просто подсказываем LLM что нужно написать полный текст
    word_triggers = ["в word", "word документ", "docx", "в ворд", "документ для скач",
                     "сделай документ", "создай документ", "создай отчёт", "создай отчет",
                     "сделай отчёт", "сделай отчет", "для скачки", "скачать документ",
                     "создай мне документ", "сделай мне документ", "напиши документ",
                     "подготовь документ", "сгенерируй документ",
                     "напиши в word", "создай word", "сохрани в word", "экспортируй в word"]
    if "file_gen" not in disabled and any(t in ql for t in word_triggers):
        parts.append("SKILL_HINT: Пользователь хочет Word документ для скачивания. Напиши ПОЛНЫЙ развёрнутый текст документа. После ответа файл .docx будет создан автоматически.")

    excel_triggers = ["в excel", "в эксель", "xlsx", "создай таблицу", "сделай таблицу",
                      "создай excel", "сделай excel", "сохрани в excel", "экспортируй в excel",
                      "excel файл", "таблицу для скач", "скачать таблицу"]
    if "file_gen" not in disabled and any(t in ql for t in excel_triggers):
        parts.append("SKILL_HINT: Пользователь хочет Excel файл. Напиши данные в формате markdown-таблицы (| col1 | col2 |). После ответа файл .xlsx будет создан автоматически.")

    # ─── 🌍 Переводчик ───
    translate_triggers = ["переведи на ", "переведи в ", "translate to ", "перевод на ", "переведи текст"]
    if "translator" not in disabled:
     for t in translate_triggers:
      if t in ql:
            try:
                after = user_input[ql.find(t) + len(t):].strip()
                lang_text = after.split(":", 1) if ":" in after else after.split(" ", 1)
                target_lang = lang_text[0].strip() if lang_text else "english"
                text_to_translate = lang_text[1].strip() if len(lang_text) > 1 else ""
                if text_to_translate and len(text_to_translate) > 2:
                    from app.services.skills_extra import translate_text
                    result = translate_text(text_to_translate, target_lang)
                    if result.get("ok"):
                        parts.append(f"Перевод ({target_lang}):\n{result.get('translated', '')}")
            except Exception as e:
                parts.append(f"SKILL_ERROR:🌍 Перевод: {e}")
            break

    # ─── 🔐 Шифрование ───
    if "encrypt" not in disabled and any(t in ql for t in ["зашифруй", "шифрование", "encrypt"]):
        try:
            from app.services.skills_extra import encrypt_text
            text = user_input
            for t in ["зашифруй:", "зашифруй ", "encrypt:", "encrypt "]:
                idx = ql.find(t)
                if idx >= 0:
                    text = user_input[idx + len(t):].strip()
                    break
            if text and len(text) > 1:
                result = encrypt_text(text)
                if result.get("ok"):
                    parts.append(f"🔐 Зашифровано:\n`{result.get('encrypted','')}`\n\nДля расшифровки скажи: расшифруй [токен]")
        except Exception as e:
            parts.append(f"SKILL_ERROR:🔐 Шифрование: {e}")

    if "encrypt" not in disabled and any(t in ql for t in ["расшифруй", "дешифруй", "decrypt"]):
        try:
            from app.services.skills_extra import decrypt_text
            token = user_input
            for t in ["расшифруй:", "расшифруй ", "decrypt:", "decrypt ", "дешифруй "]:
                idx = ql.find(t)
                if idx >= 0:
                    token = user_input[idx + len(t):].strip()
                    break
            if token:
                result = decrypt_text(token)
                if result.get("ok"):
                    parts.append(f"🔓 Расшифровано: {result.get('decrypted','')}")
                else:
                    parts.append(f"SKILL_ERROR:🔓 Расшифровка: {result.get('error','')}")
        except Exception as e:
            parts.append(f"SKILL_ERROR:🔓 Ошибка: {e}")

    # ─── 📦 Архиватор ───
    zip_triggers = ["запакуй", "архивируй", "создай архив", "создай zip", "сделай zip"]
    if "archiver" not in disabled and any(t in ql for t in zip_triggers):
        try:
            from app.services.skills_extra import create_zip
            path = user_input
            for t in zip_triggers:
                idx = ql.find(t)
                if idx >= 0:
                    path = user_input[idx + len(t):].strip().strip(":").strip()
                    break
            if path:
                result = create_zip(path)
                if result.get("ok"):
                    parts.append(f"FILE_GENERATED:zip:{result.get('download_url','')}:{result.get('filename','')}")
                else:
                    parts.append(f"SKILL_ERROR:📦 Архив: {result.get('error')}")
        except Exception as e:
            parts.append(f"SKILL_ERROR:📦 Архив: {e}")

    unzip_triggers = ["распакуй", "разархивируй", "извлеки архив"]
    if "archiver" not in disabled and any(t in ql for t in unzip_triggers):
        try:
            from app.services.skills_extra import extract_zip
            path = user_input
            for t in unzip_triggers:
                idx = ql.find(t)
                if idx >= 0:
                    path = user_input[idx + len(t):].strip().strip(":").strip()
                    break
            if path:
                result = extract_zip(path)
                if result.get("ok"):
                    parts.append(f"📦 Распаковано в {result.get('dest','')}: {result.get('count',0)} файлов")
        except Exception as e:
            parts.append(f"SKILL_ERROR:📦 Распаковка: {e}")

    # ─── 🔄 Конвертер ───
    convert_triggers = ["конвертируй", "преобразуй", "конвертировать", "convert "]
    if "converter" not in disabled and any(t in ql for t in convert_triggers):
        try:
            from app.services.skills_extra import convert_file
            # Парсим: "конвертируй data.csv в xlsx"
            match = _re.search(r"(\S+\.\w+)\s+в\s+(\w+)", user_input, _re.IGNORECASE)
            if not match:
                match = _re.search(r"(\S+\.\w+)\s+to\s+(\w+)", user_input, _re.IGNORECASE)
            if match:
                result = convert_file(match.group(1), match.group(2))
                if result.get("ok"):
                    parts.append(f"FILE_GENERATED:convert:{result.get('download_url','')}:{result.get('filename','')}")
                else:
                    parts.append(f"SKILL_ERROR:🔄 Конвертация: {result.get('error')}")
        except Exception as e:
            parts.append(f"SKILL_ERROR:🔄 Конвертация: {e}")

    # ─── 📐 Regex ───
    regex_triggers = ["проверь regex", "тест regex", "regex тест", "test regex", "регулярка", "регулярное выражение"]
    if "regex" not in disabled and any(t in ql for t in regex_triggers):
        try:
            from app.services.skills_extra import test_regex
            # Парсим: "проверь regex \d+ на строке abc123def"
            match = _re.search(r"regex[:\s]+(.+?)\s+(?:на строке|на тексте|on|text)[:\s]+(.+)", user_input, _re.IGNORECASE)
            if not match:
                match = _re.search(r"регуляр\S*[:\s]+(.+?)\s+(?:на|в|for)[:\s]+(.+)", user_input, _re.IGNORECASE)
            if match:
                result = test_regex(match.group(1).strip(), match.group(2).strip())
                if result.get("ok"):
                    matches = result.get("matches", [])
                    parts.append(f"📐 Regex `{match.group(1).strip()}`: {result.get('count',0)} совпадений\n" +
                                 "\n".join(f"  • `{m['match']}` (позиция {m['start']}-{m['end']})" for m in matches[:10]))
        except Exception as e:
            parts.append(f"SKILL_ERROR:📐 Regex: {e}")

    # ─── 📈 CSV анализ ───
    csv_triggers = ["проанализируй csv", "анализ csv", "статистика csv", "analyze csv", "проанализируй файл", "покажи статистику"]
    if "csv_analysis" not in disabled and any(t in ql for t in csv_triggers):
        try:
            from app.services.skills_extra import analyze_csv
            # Ищем имя файла
            file_match = _re.search(r"(\S+\.csv)", user_input, _re.IGNORECASE)
            if file_match:
                result = analyze_csv(file_match.group(1))
                if result.get("ok"):
                    shape = result.get("shape", {})
                    desc = result.get("describe", {})
                    parts.append(f"📈 CSV: {result.get('filename','')} — {shape.get('rows',0)} строк × {shape.get('columns',0)} колонок\n"
                                 f"Колонки: {', '.join(result.get('columns',[]))}\n"
                                 f"Пустые: {json.dumps(result.get('nulls',{}), ensure_ascii=False)}\n"
                                 f"Статистика: {json.dumps(desc, ensure_ascii=False, indent=2)[:2000]}")
        except Exception as e:
            parts.append(f"SKILL_ERROR:📈 CSV: {e}")

    # ─── 📡 Webhook ───
    webhook_triggers = ["покажи вебхуки", "покажи webhook", "что пришло на webhook", "список вебхуков"]
    if "webhook" not in disabled and any(t in ql for t in webhook_triggers):
        try:
            from app.services.skills_extra import list_webhooks
            result = list_webhooks(10)
            items = result.get("items", [])
            if items:
                lines = [f"📡 Webhook ({len(items)} последних):"]
                for w in items[-5:]:
                    lines.append(f"  • [{w.get('source','')}] {w.get('received_at','')} — {json.dumps(w.get('data',{}), ensure_ascii=False)[:200]}")
                parts.append("\n".join(lines))
            else:
                parts.append("📡 Вебхуки пусты. Отправь POST на /api/extra/webhook/{source}")
        except Exception as e:
            parts.append(f"SKILL_ERROR:📡 Webhook: {e}")

    # ─── 🔌 Плагины v2 ───
    if "plugins" not in disabled:
        try:
            from app.services.plugin_system import list_plugins, run_plugin, run_triggered, fire_hook

            # 1. Список плагинов
            plugin_list_triggers = ["список плагинов", "покажи плагины", "plugins list", "мои плагины"]
            if any(t in ql for t in plugin_list_triggers):
                result = list_plugins()
                plugins = result.get("plugins", [])
                if plugins:
                    lines = [f"🔌 Плагины ({len(plugins)}):"]
                    for p in plugins:
                        status = "✅" if p.get("enabled") else "⛔"
                        lines.append(f"  {status} {p.get('icon','🔌')} {p['name']} v{p.get('version','1.0')} — {p.get('description','')}")
                    parts.append("\n".join(lines))
                else:
                    parts.append("🔌 Плагинов нет. Положи .py файлы в data/plugins/")

            # 2. Запуск плагина вручную
            run_plugin_triggers = ["запусти плагин", "выполни плагин", "run plugin"]
            if any(t in ql for t in run_plugin_triggers):
                name_match = _re.search(r"плагин\s+(\S+)", user_input, _re.IGNORECASE)
                if not name_match:
                    name_match = _re.search(r"plugin\s+(\S+)", user_input, _re.IGNORECASE)
                if name_match:
                    result = run_plugin(name_match.group(1), {"text": user_input})
                    parts.append(f"🔌 {name_match.group(1)}: {json.dumps(result, ensure_ascii=False)[:2000]}")

            # 3. Авто-триггеры — плагины сами определяют на что реагировать
            triggered = run_triggered(user_input)
            for tr in triggered:
                parts.append(f"🔌 [{tr['plugin']}]: {json.dumps(tr, ensure_ascii=False)[:2000]}")

            # 4. on_message хук — каждый плагин может добавить контекст
            hook_results = fire_hook("on_message", user_input)
            for hr in hook_results:
                if hr.get("result"):
                    parts.append(f"🔌 [{hr['plugin']}]: {hr['result']}")

        except Exception as e:
            parts.append(f"SKILL_ERROR:🔌 Плагины: {e}")

    # ─── 📑 PDF Pro ───
    pdf_word_triggers = ["конвертируй pdf в word", "pdf в word", "pdf to word", "pdf в docx"]
    if any(t in ql for t in pdf_word_triggers):
        parts.append("SKILL_HINT: Чтобы конвертировать PDF в Word — загрузи PDF через кнопку + и напиши 'конвертируй в word'. PDF будет обработан автоматически через /api/pdf/to-word.")

    pdf_table_triggers = ["извлеки таблицы из pdf", "таблицы из pdf", "pdf таблицы в excel"]
    if any(t in ql for t in pdf_table_triggers):
        parts.append("SKILL_HINT: Чтобы извлечь таблицы из PDF — загрузи PDF через кнопку + и напиши 'извлеки таблицы'. Таблицы будут сохранены в Excel через /api/pdf/tables.")

    # --- Git skill ---
    _git_st = ['git status', 'статус git', 'что изменилось в git', 'покажи git', 'git изменения', 'ветка git']
    if 'git' not in disabled and any(t in ql for t in _git_st):
        try:
            from app.services.git_service import format_git_context
            parts.append(format_git_context())
        except Exception as _e:
            parts.append('SKILL_ERROR:Git: ' + str(_e))
    _git_lg = ['git log', 'история коммитов', 'последние коммиты', 'покажи коммиты']
    if 'git' not in disabled and any(t in ql for t in _git_lg):
        try:
            from app.services.git_service import git_log as _gl
            _r = _gl(limit=10)
            if _r.get('ok'):
                _rows = ['Git log (' + _r['repo'] + '):'] + ['  ' + c['hash'] + ' - ' + c['message'] for c in _r.get('commits', [])]
                parts.append(chr(10).join(_rows))
        except Exception as _e:
            parts.append('SKILL_ERROR:Git log: ' + str(_e))
    _git_df = ['git diff', 'покажи diff', 'что я изменил', 'изменения в коде']
    if 'git' not in disabled and any(t in ql for t in _git_df):
        try:
            from app.services.git_service import git_diff as _gdf
            _r = _gdf()
            if _r.get('ok'):
                parts.append('Git diff:' + chr(10) + _r.get('stat','') + chr(10) + _r.get('diff','')[:3000])
        except Exception as _e:
            parts.append('SKILL_ERROR:Git diff: ' + str(_e))

    # ─── 🎨 GPU статус ───
    gpu_triggers = ["статус gpu", "gpu status", "сколько vram", "видеопамять"]
    if any(t in ql for t in gpu_triggers):
        try:
            from app.services.image_gen import get_status
            result = get_status()
            parts.append(f"🖥 GPU: {result.get('gpu','?')}\n"
                         f"VRAM: {result.get('vram_used_mb',0)} / {result.get('vram_total_mb',0)} MB\n"
                         f"Модель загружена: {'да' if result.get('loaded') else 'нет'}")
        except Exception as e:
            parts.append(f"GPU: {e}")

    # ─── 📊 Сгенерированные файлы ───
    files_triggers = ["покажи файлы", "список файлов", "сгенерированные файлы", "мои файлы"]
    if any(t in ql for t in files_triggers):
        try:
            from app.core.config import GENERATED_DIR as gen_dir
            if gen_dir.exists():
                files = sorted(gen_dir.iterdir())[-10:]
                if files:
                    lines = ["📊 Последние файлы:"]
                    for f in files:
                        lines.append(f"  • [{f.name}]({API_BASE}/api/skills/download/{f.name}) ({f.stat().st_size} байт)")
                    parts.append("\n".join(lines))
        except Exception:
            pass

    return "\n\n".join(parts)


import json


def _is_strict_web_only_query(user_input: str) -> bool:
    q = (user_input or "").lower()
    hard_terms = (
        "новост", "news", "курс", "доллар", "евро", "рубл", "тенге",
        "usd", "eur", "kzt", "погод", "weather", "сегодня", "today",
        "сейчас", "current", "актуальн", "latest", "последние"
    )
    return any(term in q for term in hard_terms)



def _get_web_search_result(tool_results):
    for item in reversed(tool_results or []):
        if item.get("tool") == "web_search":
            result = item.get("result") or {}
            if isinstance(result, dict):
                return result
    return {}



def _build_prompt(user_input, context_bundle, mode="default", disabled_skills: set | None = None):
    from datetime import datetime
    days_ru = {"Monday": "понедельник", "Tuesday": "вторник", "Wednesday": "среда", "Thursday": "четверг", "Friday": "пятница", "Saturday": "суббота", "Sunday": "воскресенье"}
    now = datetime.now()
    day_name = days_ru.get(now.strftime("%A"), now.strftime("%A"))
    time_line = f"Сейчас: {now.strftime('%d.%m.%Y, %H:%M')}, {day_name}."

    # Авто-скиллы
    skill_results = _run_auto_skills(user_input, disabled=disabled_skills or set())

    # Отделяем картинки/файлы — они не идут в LLM контекст, а добавляются к ответу
    _pending_attachments.clear()
    if skill_results:
        clean_parts = []
        for line in skill_results.split("\n\n"):
            if line.startswith("IMAGE_GENERATED:"):
                # IMAGE_GENERATED:view_url:filename:prompt
                p = line.split(":", 4)
                if len(p) >= 4:
                    _pending_attachments.append({
                        "type": "image",
                        "view_url": p[1] + ":" + p[2] if "http" in p[1] else p[1],
                        "filename": p[2] if "http" not in p[1] else p[3],
                        "prompt": p[-1],
                    })
            elif line.startswith("FILE_GENERATED:"):
                # FILE_GENERATED:type:download_url:filename
                p = line.split(":", 4)
                if len(p) >= 4:
                    _pending_attachments.append({
                        "type": "file",
                        "file_type": p[1],
                        "download_url": p[2] + ":" + p[3] if "http" in p[2] else p[2],
                        "filename": p[3] if "http" not in p[2] else p[4] if len(p) > 4 else p[3],
                    })
            elif line.startswith("SKILL_HINT:"):
                clean_parts.append(line)  # подсказки для LLM оставляем
            elif line.startswith("SKILL_ERROR:"):
                # Ошибки скиллов НЕ идут в LLM — показываем пользователю напрямую
                error_msg = line[len("SKILL_ERROR:"):]
                _pending_attachments.append({"type": "error", "message": error_msg})
            else:
                clean_parts.append(line)
        skill_results = "\n\n".join(clean_parts)

    if skill_results:
        context_bundle = (context_bundle + "\n\n" + skill_results) if context_bundle.strip() else skill_results

    if not context_bundle.strip():
        return f"{time_line}\n\n{user_input}"
    return (
        f"{time_line}\n\n"
        "Вот данные из интернета и других источников:\n\n"
        + context_bundle
        + "\n\n---\n\n"
        "Вопрос пользователя: " + user_input + "\n\n"
        "ПРАВИЛА ОТВЕТА:\n"
        "1. ОБЯЗАТЕЛЬНО используй данные выше для ответа — они собраны из нескольких поисковиков.\n"
        "2. Если есть секция «СОДЕРЖИМОЕ ВЕБ-СТРАНИЦ» — это ГЛАВНЫЙ источник, цитируй оттуда.\n"
        "3. Если есть «СВЕЖИЕ НОВОСТИ» — упомяни актуальные события по теме.\n"
        "4. Приводи конкретные факты, даты и цифры из данных выше, но без служебных маркеров и внутреннего контекста.\n"
        "5. Не вставляй URL и список источников, если пользователь прямо не попросил ссылки или источники.\n"
        "6. Если свежесть данных под вопросом, честно скажи об этом простыми словами.\n"
        "7. Не говори что данных нет, если они есть выше."
    )


# Хранилище для вложений (картинки, файлы) которые добавляются ПОСЛЕ ответа LLM
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
# ═══════════════════════════════════════════════════════════════

def _fetch_page_text(url, max_chars=4000):
    """Заходит на сайт и извлекает основной текст. Улучшенная версия."""
    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "ru,en;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return ""

        # Пробуем определить кодировку
        if resp.encoding and resp.encoding.lower() != "utf-8":
            resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")

        # Удаляем мусор
        for tag in soup(["script", "style", "nav", "header", "footer", "aside",
                         "form", "button", "iframe", "noscript", "svg", "img",
                         "menu", "advertisement", "ad", "banner"]):
            tag.decompose()

        # Удаляем элементы с рекламными классами
        for el in soup.select("[class*='advert'], [class*='banner'], [class*='cookie'], [class*='popup'], [class*='modal'], [id*='advert'], [id*='banner']"):
            el.decompose()

        # Ищем основной контент (приоритет по порядку)
        content_selectors = [
            "article", "main", "[role='main']",
            ".article-body", ".article-content", ".post-content", ".entry-content",
            ".news-body", ".story-body", ".text-content",
            ".content", "#content", "#main-content",
        ]
        main_el = None
        for sel in content_selectors:
            main_el = soup.select_one(sel)
            if main_el and len(main_el.get_text(strip=True)) > 100:
                break
            main_el = None

        if main_el:
            text = main_el.get_text(separator="\n", strip=True)
        else:
            # Fallback: берём body, но убираем короткие строки (навигация)
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n", strip=True)
            else:
                text = soup.get_text(separator="\n", strip=True)

        # Убираем пустые и слишком короткие строки (навигация, кнопки)
        lines = []
        for line in text.split("\n"):
            line = line.strip()
            if len(line) > 20:  # Пропускаем "Главная", "Меню", "Войти" и т.д.
                lines.append(line)

        text = "\n".join(lines)
        return text[:max_chars] if text else ""
    except Exception as e:
        return ""


_WEB_SKIP_FETCH_DOMAINS = [
    "youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "vk.com", "t.me", "pinterest.com",
]


def _count_hits_for_domains(items, preferred_domains):
    try:
        from app.core.web import count_preferred_domain_hits
        return count_preferred_domain_hits(items, preferred_domains)
    except Exception:
        return 0


def _build_single_web_subquery_context(subquery):
    from app.core.web import fetch_page_text as core_fetch
    from app.core.web import research_web, search_news as core_search_news, search_web as core_search

    query = subquery.get("query", "")
    label = subquery.get("label", "Поиск")
    intent_kind = subquery.get("intent_kind", "")
    geo_scope = subquery.get("geo_scope", "")
    local_first = bool(subquery.get("local_first"))
    needs_news_feed = bool(subquery.get("needs_news_feed"))
    needs_deep_search = bool(subquery.get("needs_deep_search"))
    preferred_domains = tuple(subquery.get("preferred_domains", []) or [])

    search_results = core_search(
        query,
        max_results=6,
        intent_kind=intent_kind,
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=preferred_domains,
    )
    normalized_search = [
        {
            "title": item.get("title", ""),
            "url": item.get("href", ""),
            "snippet": item.get("body", ""),
            "engine": item.get("engine", ""),
        }
        for item in search_results
        if item.get("href", "").startswith("http")
    ]

    news_results = []
    if needs_news_feed:
        raw_news = core_search_news(
            query,
            max_results=5,
            intent_kind=intent_kind,
            geo_scope=geo_scope,
            local_first=local_first,
            preferred_domains=preferred_domains,
        )
        for item in raw_news:
            href = item.get("href") or item.get("url") or ""
            if href.startswith("http"):
                news_results.append(
                    {
                        "title": item.get("title", ""),
                        "url": href,
                        "snippet": item.get("body", ""),
                        "date": item.get("date", ""),
                        "source": item.get("source", ""),
                        "engine": item.get("engine", "ddg-news"),
                    }
                )

    fetch_candidates = []
    seen_urls = set()
    for item in normalized_search:
        url = item["url"]
        if not url or url in seen_urls or any(domain in url for domain in _WEB_SKIP_FETCH_DOMAINS):
            continue
        seen_urls.add(url)
        fetch_candidates.append(item)
        if len(fetch_candidates) >= 4:
            break

    deep_content = []
    fetched_urls = set()
    for item in fetch_candidates[:2]:
        text = (core_fetch(item["url"]) or "")[:3000]
        if text and len(text) > 100:
            deep_content.append("--- " + item["title"] + " ---\n" + text)
            fetched_urls.add(item["url"])

    local_source_hits = _count_hits_for_domains(
        [{"href": item.get("url", "")} for item in normalized_search + news_results],
        preferred_domains,
    )
    weak_coverage = (
        len(normalized_search) < 3
        or (needs_news_feed and not news_results)
        or (local_first and preferred_domains and local_source_hits == 0)
    )

    deeper_search = False
    deep_context = ""
    if needs_deep_search and weak_coverage:
        deep_engines = ("wikipedia", "tavily", "duckduckgo") if intent_kind == "historical" else ("tavily", "duckduckgo", "wikipedia")
        deep_context = research_web(
            query,
            max_results=6,
            pages_to_read=3,
            engines=deep_engines,
            intent_kind=intent_kind,
            geo_scope=geo_scope,
            local_first=local_first,
            preferred_domains=preferred_domains,
        )
        deeper_search = bool(deep_context)

    parts = [f"=== ПОДТЕМА: {label} ===", f"Запрос: {query}"]

    if deep_content:
        parts.append("СОДЕРЖИМОЕ ВЕБ-СТРАНИЦ:\n" + "\n\n".join(deep_content))

    if news_results:
        lines = []
        for item in news_results[:5]:
            date_str = f" [{item['date']}]" if item.get("date") else ""
            source_str = f" ({item['source']})" if item.get("source") else ""
            lines.append(f"- {item['title']}{date_str}{source_str}: {item['snippet']}")
        parts.append("СВЕЖИЕ НОВОСТИ:\n" + "\n".join(lines))

    remaining = [item for item in normalized_search if item["url"] not in fetched_urls][:4]
    if remaining:
        lines = [f"- {item['title']}: {item['snippet']}" for item in remaining]
        parts.append("ОСТАЛЬНЫЕ РЕЗУЛЬТАТЫ:\n" + "\n".join(lines))

    if deep_context:
        parts.append("УГЛУБЛЕННЫЙ ПОИСК:\n" + deep_context)

    if not normalized_search and not news_results and not deep_context:
        parts.append("Недостаточно свежих подтвержденных данных по этой подтеме.")

    engines_used = sorted(
        {
            item.get("engine", "")
            for item in normalized_search + news_results
            if item.get("engine")
        }
    )

    return {
        "context": "\n\n".join(part for part in parts if part.strip()),
        "debug": {
            "label": label,
            "query": query,
            "intent_kind": intent_kind,
            "geo_scope": geo_scope,
            "found": len(normalized_search),
            "news_hits": len(news_results),
            "fetched_pages": len(deep_content),
            "engines": engines_used,
            "local_source_hits": local_source_hits,
            "deeper_search_used": deeper_search,
            "coverage": "strong" if (len(normalized_search) >= 3 or news_results or deep_content) else "weak",
        },
    }


def _do_web_search_legacy(query, timeline, tool_results):
    """
    Multi-engine поиск: DDG + Bing + Google + Yandex + DDG News.
    Параллельный fetch top-3 страниц через BeautifulSoup.
    Использует core/web.py для мульти-поиска.
    """
    search_query = _clean_query(query)

    # ═══ Шаг 1: Multi-engine поиск ═══
    search_results = []
    engines_used = []
    try:
        from app.core.web import fetch_page_text as core_fetch
        from app.core.web import search_news as core_search_news
        from app.core.web import search_web as multi_search
        raw = multi_search(search_query, max_results=12)
        for r in raw:
            href = r.get("href", "")
            if href and href.startswith("http"):
                search_results.append({
                    "title": r.get("title", ""),
                    "url": href,
                    "snippet": r.get("body", ""),
                    "engine": r.get("engine", ""),
                })
        engines_used = sorted({r.get("engine", "") for r in raw if r.get("engine")})
    except Exception as e:
        logger.warning(f"Web search failed: {e}")

    # Fallback: только DDG если мульти-поиск упал
    if not search_results:
        try:
            DDGS = None
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                raw = list(ddgs.text(search_query, max_results=8))
            for r in raw:
                url = r.get("href") or r.get("url") or ""
                if url:
                    search_results.append({"title": r.get("title", ""), "url": url, "snippet": r.get("body", ""), "engine": "duckduckgo"})
            engines_used = ["duckduckgo"]
        except Exception as e:
            logger.warning(f"DDG fallback also failed: {e}")

    # ═══ Шаг 1.5: DDG News (свежие новости) ═══
    news_results = []
    try:
        news_raw = core_search_news(search_query, max_results=5)
        for n in news_raw:
            url = n.get("href") or n.get("url") or ""
            if url and url.startswith("http"):
                news_results.append({
                    "title": n.get("title", ""),
                    "url": url,
                    "snippet": n.get("body", ""),
                    "date": n.get("date", ""),
                    "source": n.get("source", ""),
                })
        if news_results and "ddg-news" not in engines_used:
            engines_used.append("ddg-news")
    except Exception:
        pass  # Новости — бонус, не критично

    if not search_results and not news_results:
        _tl(timeline, "tool_web", "Веб-поиск", "error", "Нет результатов")
        tool_results.append({"tool": "web_search", "result": {"count": 0}})
        return "[Поиск не дал результатов]"

    # ═══ Шаг 2: Deep fetch top-3 страниц (параллельно) ═══
    deep_content = []
    fetched_urls = set()
    skip_domains = ["youtube.com", "youtu.be", "facebook.com", "instagram.com",
                    "tiktok.com", "twitter.com", "x.com", "vk.com", "t.me",
                    "pinterest.com"]

    # Дедупликация URL, фильтр соцсетей
    all_urls_seen = set()
    fetch_candidates = []
    for item in search_results[:7]:
        url = item["url"]
        if url not in all_urls_seen and not any(d in url for d in skip_domains):
            all_urls_seen.add(url)
            fetch_candidates.append(item)

    # Параллельный fetch через ThreadPoolExecutor
    from concurrent.futures import ThreadPoolExecutor, as_completed
    targets = fetch_candidates[:5]  # Пробуем 5, берём лучшие 3
    if targets:
        page_results = {}  # url → text
        with ThreadPoolExecutor(max_workers=min(len(targets), 4)) as executor:
            future_map = {executor.submit(core_fetch, t["url"]): t for t in targets}
            for future in as_completed(future_map):
                item = future_map[future]
                try:
                    text = (future.result() or "")[:3000]
                    if text and len(text) > 100 and not text.lower().startswith("рћс€рёр±рєр°"):
                        page_results[item["url"]] = (item, text)
                except Exception:
                    pass

        # Берём первые 3 успешных (по порядку оригинальных результатов)
        for t in targets:
            if t["url"] in page_results and len(deep_content) < 3:
                item, text = page_results[t["url"]]
                deep_content.append(
                    "--- " + item["title"] + " ---\n"
                    + text
                )
                fetched_urls.add(item["url"])

    fetched_count = len(deep_content)

    # ═══ Шаг 3: Формируем контекст ═══
    engines_str = ", ".join(engines_used) if engines_used else "search"
    tool_results.append({"tool": "web_search", "result": {
        "query": search_query,
        "found": len(search_results),
        "news": len(news_results),
        "fetched_pages": fetched_count,
        "engines": engines_used,
    }})
    _tl(timeline, "tool_web", "Веб-поиск", "done",
        f"{len(search_results)} найдено ({engines_str}), {fetched_count} страниц загружено, {len(news_results)} новостей")

    parts = []

    # Глубокий контент (со страниц)
    if deep_content:
        parts.append("══ СОДЕРЖИМОЕ ВЕБ-СТРАНИЦ (ИСПОЛЬЗУЙ ЭТИ ДАННЫЕ!) ══\n\n" + "\n\n".join(deep_content))

    # Свежие новости
    if news_results:
        news_lines = []
        for n in news_results[:5]:
            date_str = f" [{n['date']}]" if n.get("date") else ""
            source_str = f" ({n['source']})" if n.get("source") else ""
            news_lines.append(f"- {n['title']}{date_str}{source_str}: {n['snippet']}")
        parts.append("══ СВЕЖИЕ НОВОСТИ ══\n" + "\n".join(news_lines))

    # Сниппеты остальных результатов (исключаем уже загруженные)
    remaining = [s for s in search_results if s["url"] not in fetched_urls][:5]
    if remaining:
        snippet_lines = [f"- {s['title']}: {s['snippet']}" for s in remaining]
        parts.append("══ ДРУГИЕ РЕЗУЛЬТАТЫ ══\n" + "\n".join(snippet_lines))

    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# КОНТЕКСТ
# ═══════════════════════════════════════════════════════════════

def _do_temporal_web_search_legacy(query, timeline, tool_results, temporal=None):
    temporal = temporal or {}
    context = _do_web_search_legacy(query, timeline, tool_results)
    web_result = _get_web_search_result(tool_results)
    found = int(web_result.get("found", 0) or 0)
    fetched_pages = int(web_result.get("fetched_pages", 0) or 0)
    news_count = int(web_result.get("news", 0) or 0)
    engines_used = set(web_result.get("engines", []) or [])
    current_evidence_engines = {"tavily", "duckduckgo", "ddg-news"}
    has_current_evidence = bool(engines_used & current_evidence_engines) or news_count > 0
    deeper_search = False

    if temporal.get("requires_web") and temporal.get("reasoning_depth") == "deep":
        weak_coverage = found < 4 or fetched_pages < 2 or (temporal.get("freshness_sensitive") and not has_current_evidence)
        if weak_coverage:
            try:
                from app.core.web import research_web
                deep_engines = ("wikipedia", "tavily", "duckduckgo") if temporal.get("stable_historical") else ("tavily", "duckduckgo", "wikipedia")

                deep_context = research_web(
                    _clean_query(query),
                    max_results=8,
                    pages_to_read=4,
                    engines=deep_engines,
                )
                if deep_context:
                    deeper_search = True
                    context = (
                        context + "\n\nДополнительный углубленный веб-поиск:\n" + deep_context
                        if context
                        else deep_context
                    )
                    _tl(timeline, "tool_web_deep", "Углубленный веб-поиск", "done", "Дополнительная проверка источников")
            except Exception as exc:
                _tl(timeline, "tool_web_deep", "Углубленный веб-поиск", "error", str(exc))

    if temporal.get("freshness_sensitive"):
        freshness_state = "fresh_checked" if has_current_evidence and (news_count > 0 or fetched_pages >= 2 or deeper_search) else "unverified_current"
        freshness_note = (
            "Freshness status: fresh_checked. Use current web findings as the main evidence."
            if freshness_state == "fresh_checked"
            else "Freshness status: unverified_current. If confidence is limited, say that the data may be outdated or not fully verified."
        )
    elif temporal.get("stable_historical"):
        freshness_state = "historical_or_stable"
        freshness_note = "Freshness status: historical_or_stable. Treat this as a mostly stable historical topic."
    else:
        freshness_state = "standard_web"
        freshness_note = "Freshness status: standard_web. Use the web findings naturally without exposing internal formatting."

    if tool_results and tool_results[-1].get("tool") == "web_search":
        result = tool_results[-1].setdefault("result", {})
        if isinstance(result, dict):
            result["freshness_state"] = freshness_state
            result["deeper_search"] = deeper_search
            result["temporal_mode"] = temporal.get("mode", "none")
            result["has_current_evidence"] = has_current_evidence

    if context:
        context += "\n\n" + freshness_note
    return context


def _do_web_search(query, timeline, tool_results, web_plan=None):
    search_query = _clean_query(query)
    plan = web_plan or {
        "is_multi_intent": False,
        "subqueries": [
            {
                "label": "Web search",
                "query": search_query,
                "intent_kind": "general_web",
                "geo_scope": "",
                "freshness_class": "stable",
                "local_first": False,
                "needs_news_feed": False,
                "needs_deep_search": False,
                "preferred_domains": [],
            }
        ],
    }

    raw_subqueries = list(plan.get("subqueries") or [])[:6]
    if not raw_subqueries:
        raw_subqueries = [
            {
                "label": "Web search",
                "query": search_query,
                "intent_kind": "general_web",
                "geo_scope": "",
                "freshness_class": "stable",
                "local_first": False,
                "needs_news_feed": False,
                "needs_deep_search": False,
                "preferred_domains": [],
                "priority": 0,
            }
        ]

    passes = list(plan.get("passes") or [])
    if not passes:
        passes = [
            {
                "name": f"pass_{pass_index + 1}",
                "subqueries": raw_subqueries[offset : offset + 3],
            }
            for pass_index, offset in enumerate(range(0, len(raw_subqueries), 3))
        ]

    sections = []
    debug_rows = []
    pass_summaries = []
    engines_used = set()
    total_found = 0
    total_news = 0
    total_fetched = 0
    total_local_hits = 0
    deeper_search_used = False
    uncovered_subqueries = list(plan.get("uncovered_subqueries") or [])

    for pass_index, pass_spec in enumerate(passes, start=1):
        pass_name = str(pass_spec.get("name") or f"pass_{pass_index}")
        pass_found = 0
        pass_news = 0
        pass_pages = 0
        pass_engines = set()
        pass_queries = []
        pass_uncovered = []

        for subquery in list(pass_spec.get("subqueries") or [])[:3]:
            subquery_result = _build_single_web_subquery_context(subquery)
            context = (subquery_result.get("context") or "").strip()
            debug = dict(subquery_result.get("debug") or {})
            debug["pass_name"] = pass_name
            debug_rows.append(debug)
            pass_queries.append(debug.get("query", ""))

            if context:
                sections.append(context)

            found = int(debug.get("found", 0) or 0)
            news_hits = int(debug.get("news_hits", 0) or 0)
            fetched_pages = int(debug.get("fetched_pages", 0) or 0)
            local_hits = int(debug.get("local_source_hits", 0) or 0)
            coverage = str(debug.get("coverage", "weak") or "weak")

            total_found += found
            total_news += news_hits
            total_fetched += fetched_pages
            total_local_hits += local_hits
            deeper_search_used = deeper_search_used or bool(debug.get("deeper_search_used"))
            engines_used.update(debug.get("engines", []) or [])

            pass_found += found
            pass_news += news_hits
            pass_pages += fetched_pages
            pass_engines.update(debug.get("engines", []) or [])

            if coverage != "strong":
                pass_uncovered.append(debug.get("query", ""))
                uncovered_subqueries.append(debug.get("query", ""))

            if found or news_hits or fetched_pages:
                _tl(
                    timeline,
                    f"tool_web_{pass_name}_{len(pass_queries)}",
                    f"Веб-поиск {pass_name}",
                    "done",
                    f"{debug.get('query', '')}: found={found}, news={news_hits}, pages={fetched_pages}",
                )
            else:
                _tl(
                    timeline,
                    f"tool_web_{pass_name}_{len(pass_queries)}",
                    f"Веб-поиск {pass_name}",
                    "error",
                    f"{debug.get('query', '')}: no confirmed results",
                )

        pass_summaries.append(
            {
                "name": pass_name,
                "subqueries": pass_queries,
                "found": pass_found,
                "news_hits": pass_news,
                "fetched_pages": pass_pages,
                "engines": sorted(pass_engines),
                "uncovered_subqueries": [item for item in pass_uncovered if item],
            }
        )
        _tl(
            timeline,
            f"tool_web_{pass_name}",
            f"Веб-проход {pass_index}",
            "done",
            f"{len(pass_queries)} подтем, found={pass_found}, news={pass_news}, pages={pass_pages}",
        )

    unique_uncovered = list(dict.fromkeys(item for item in uncovered_subqueries if item))
    result_payload = {
        "query": search_query,
        "count": total_found,
        "found": total_found,
        "news": total_news,
        "fetched_pages": total_fetched,
        "engines": sorted(engines_used),
        "subqueries": [debug.get("query", "") for debug in debug_rows],
        "coverage_by_subquery": {
            debug.get("query", f"subquery_{idx + 1}"): debug.get("coverage", "weak")
            for idx, debug in enumerate(debug_rows)
        },
        "engines_by_subquery": {
            debug.get("query", f"subquery_{idx + 1}"): debug.get("engines", [])
            for idx, debug in enumerate(debug_rows)
        },
        "local_source_hits": total_local_hits,
        "news_hits": total_news,
        "deeper_search_used": deeper_search_used,
        "is_multi_intent": bool(plan.get("is_multi_intent")),
        "passes": pass_summaries,
        "pass_count": len(pass_summaries),
        "total_subqueries": len(raw_subqueries),
        "overflow_applied": bool(plan.get("overflow_applied") or len(raw_subqueries) > 3),
        "uncovered_subqueries": unique_uncovered,
    }
    tool_results.append({"tool": "web_search", "result": result_payload})

    if not sections:
        _tl(timeline, "tool_web", "Веб-поиск", "error", "Нет подтвержденных результатов")
        return "[Поиск не дал результатов]"

    _tl(
        timeline,
        "tool_web",
        "Веб-поиск",
        "done",
        f"{total_found} найдено, {total_news} новостей, {total_fetched} страниц, {len(raw_subqueries)} подтем, {len(pass_summaries)} проходов",
    )
    return "\n\n".join(section for section in sections if section.strip())


def _do_temporal_web_search(query, timeline, tool_results, temporal=None, web_plan=None):
    temporal = temporal or {}
    context = _do_web_search(query, timeline, tool_results, web_plan=web_plan)
    web_result = _get_web_search_result(tool_results)
    found = int(web_result.get("found", 0) or 0)
    fetched_pages = int(web_result.get("fetched_pages", 0) or 0)
    news_count = int(web_result.get("news", 0) or 0)
    subquery_count = int(web_result.get("total_subqueries", len(web_result.get("subqueries", []) or [])) or 0)
    engines_used = set(web_result.get("engines", []) or [])
    current_evidence_engines = {"tavily", "duckduckgo", "ddg-news"}
    has_current_evidence = bool(engines_used & current_evidence_engines) or news_count > 0
    deeper_search = bool(web_result.get("deeper_search_used"))

    if temporal.get("requires_web") and temporal.get("reasoning_depth") == "deep":
        weak_coverage = (
            found < max(4, subquery_count * 2)
            or fetched_pages < max(2, subquery_count)
            or (temporal.get("freshness_sensitive") and not has_current_evidence)
        )
        if weak_coverage:
            try:
                from app.core.web import research_web

                deep_engines = ("wikipedia", "tavily", "duckduckgo") if temporal.get("stable_historical") else ("tavily", "duckduckgo", "wikipedia")

                deep_context = research_web(
                    _clean_query(query),
                    max_results=8,
                    pages_to_read=4,
                    engines=deep_engines,
                    intent_kind="historical" if temporal.get("stable_historical") else "general_web",
                )
                if deep_context:
                    deeper_search = True
                    context = (
                        context + "\n\nДополнительный углубленный веб-поиск:\n" + deep_context
                        if context
                        else deep_context
                    )
                    _tl(timeline, "tool_web_deep", "Углубленный веб-поиск", "done", "Дополнительная проверка источников")
            except Exception as exc:
                _tl(timeline, "tool_web_deep", "Углубленный веб-поиск", "error", str(exc))

    if temporal.get("freshness_sensitive"):
        freshness_state = "fresh_checked" if has_current_evidence and (news_count > 0 or fetched_pages >= 2 or deeper_search) else "unverified_current"
        freshness_note = (
            "Freshness status: fresh_checked. Use current web findings as the main evidence."
            if freshness_state == "fresh_checked"
            else "Freshness status: unverified_current. If confidence is limited, say that the data may be outdated or not fully verified."
        )
    elif temporal.get("stable_historical"):
        freshness_state = "historical_or_stable"
        freshness_note = "Freshness status: historical_or_stable. Treat this as a mostly stable historical topic."
    else:
        freshness_state = "standard_web"
        freshness_note = "Freshness status: standard_web. Use the web findings naturally without exposing internal formatting."

    if tool_results and tool_results[-1].get("tool") == "web_search":
        result = tool_results[-1].setdefault("result", {})
        if isinstance(result, dict):
            result["freshness_state"] = freshness_state
            result["deeper_search"] = deeper_search
            result["temporal_mode"] = temporal.get("mode", "none")
            result["has_current_evidence"] = has_current_evidence

    if context:
        context += "\n\n" + freshness_note
    return context


def _collect_context_legacy(*, profile_name, user_input, tools, tool_results, timeline, use_reflection=False, temporal=None, web_plan=None):
    parts = []
    for tool_name in tools:
        try:
            if tool_name == "memory_search":
                result = run_tool("search_memory", {"profile": profile_name, "query": user_input, "limit": 5})
                tool_results.append({"tool": "search_memory", "result": result})
                items = result.get("items", [])
                _tl(timeline, "tool_memory", "Память", "done", str(result.get("count", 0)))
                if items:
                    parts.append("Из памяти:\n" + "\n".join("- " + i.get("text", "") for i in items))

            elif tool_name == "library_context":
                _tl(timeline, "tool_library", "Библиотека", "skip", "Фронтенд")

            elif tool_name == "web_search":
                web_ctx = _do_temporal_web_search(user_input, timeline, tool_results, temporal=temporal, web_plan=web_plan)
                if web_ctx:
                    parts.append(web_ctx)

            elif tool_name == "project_mode":
                project_ctx = ""
                # Попытка 1: старый project_service
                try:
                    tree = run_tool("list_project_tree", {"max_depth": 3, "max_items": 200})
                    search = run_tool("search_project", {"query": user_input, "max_hits": 20})
                    tool_results.append({"tool": "project", "result": {"tree": tree.get("count", 0), "hits": search.get("count", 0)}})
                    snippets = search.get("items") or search.get("results") or []
                    if snippets:
                        rendered = ["- " + (item.get("path","") + ": " + (item.get("snippet","") or item.get("preview","")) if isinstance(item,dict) else str(item)) for item in snippets[:10]]
                        project_ctx = "Из проекта:\n" + "\n".join(rendered)
                except Exception:
                    pass

                # Попытка 2: advanced project API (если открыт через UI)
                if not project_ctx:
                    try:
                        from app.api.routes.advanced_routes import _project_path
                        if _project_path:
                            from pathlib import Path
                            root = Path(_project_path)
                            if root.exists():
                                file_list = []
                                for f in sorted(root.rglob("*"))[:50]:
                                    if f.is_file() and not any(b in str(f) for b in [".git","node_modules","__pycache__",".venv","dist"]):
                                        file_list.append(str(f.relative_to(root)))
                                project_ctx = f"Открыт проект: {root.name}\nФайлы ({len(file_list)}):\n" + "\n".join("- " + f for f in file_list[:30])
                    except Exception:
                        pass

                if project_ctx:
                    parts.append(project_ctx)
                    _tl(timeline, "tool_project", "Проект", "done", "Контекст загружен")
                else:
                    _tl(timeline, "tool_project", "Проект", "skip", "Не открыт")

            elif tool_name == "python_executor":
                _tl(timeline, "tool_python", "Python", "ready", "Выполнение по запросу")

            elif tool_name == "project_patch":
                _tl(timeline, "tool_patch", "Патчинг", "ready", "")

        except Exception as exc:
            _tl(timeline, "tool_" + tool_name, tool_name, "error", str(exc))

    return "\n\n".join(p for p in parts if p.strip())


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

        persona_meta = observe_dialogue(
            dialog_id=run["run_id"],
            session_id=str(session_id or run["run_id"]),
            profile_name=profile_name,
            model_name=effective_model,
            user_input=raw_user_input,
            answer_text=answer,
            route=route,
            outcome_ok=True,
        )
        result = {
            "ok": True,
            "answer": answer,
            "timeline": timeline,
            "tool_results": tool_results,
            "meta": build_chat_meta(
                model_name=effective_model,
                profile_name=profile_name,
                route=route,
                tools=selected,
                run_id=run["run_id"],
                temporal=temporal,
                web_plan=web_plan,
                persona=persona_meta,
                identity_guard=identity_guard,
                provenance_guard=provenance_guard,
            ),
        }
        _HISTORY.finish_run(run["run_id"], result)
        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
        _record_agent_os_monitoring(
            agent_id=_effective_agent_id,
            run_id=run["run_id"],
            route=route,
            model_name=effective_model,
            ok=True,
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            selected_tools=selected,
        )

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
        _emit_agent_os_event(
            event_type="agent.run.completed",
            source_agent_id=_agent_os_source_id,
            payload={
                "run_id": run["run_id"],
                "profile_name": profile_name,
                "route": route,
                "ok": True,
                "model_used": effective_model,
                "duration_ms": _duration_ms,
                "session_id": str(session_id or ""),
                "streaming": False,
            },
        )

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
        _HISTORY.finish_run(run["run_id"], err)
        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)
        _record_agent_os_monitoring(
            agent_id=_effective_agent_id,
            run_id=run["run_id"],
            route=locals().get("route", ""),
            model_name=locals().get("effective_model", model_name),
            ok=False,
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            selected_tools=locals().get("selected", []),
        )
        _emit_agent_os_event(
            event_type="agent.run.completed",
            source_agent_id=_agent_os_source_id,
            payload={
                "run_id": run["run_id"],
                "profile_name": profile_name,
                "route": locals().get("route", ""),
                "ok": False,
                "model_used": locals().get("effective_model", model_name),
                "duration_ms": _duration_ms,
                "error": str(exc)[:500],
                "session_id": str(session_id or ""),
                "streaming": False,
            },
        )
        return err
    except Exception as exc:
        err = {"ok": False, "answer": "", "timeline": timeline + [{"step": "error", "title": "Ошибка", "status": "error", "detail": str(exc)}], "tool_results": tool_results, "meta": {"error": str(exc), "run_id": run["run_id"]}}
        _HISTORY.finish_run(run["run_id"], err)
        _duration_ms = int((_time.monotonic() - _agent_start) * 1000)

        # Agent OS: записываем ошибочный запуск
        _record_agent_os_monitoring(
            agent_id=_effective_agent_id,
            run_id=run["run_id"],
            route=locals().get("route", ""),
            model_name=locals().get("effective_model", model_name),
            ok=False,
            duration_ms=_duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            selected_tools=locals().get("selected", []),
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

        _emit_agent_os_event(
            event_type="agent.run.completed",
            source_agent_id=_agent_os_source_id,
            payload={
                "run_id": run["run_id"],
                "profile_name": profile_name,
                "route": locals().get("route", ""),
                "ok": False,
                "model_used": locals().get("effective_model", model_name),
                "duration_ms": _duration_ms,
                "error": str(exc)[:500],
                "session_id": str(session_id or ""),
                "streaming": False,
            },
        )

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
                persona_meta = observe_dialogue(
                    dialog_id=run["run_id"],
                    session_id=str(session_id or run["run_id"]),
                    profile_name=profile_name,
                    model_name=effective_model,
                    user_input=raw_user_input,
                    answer_text=cached,
                    route=route,
                    outcome_ok=True,
                )
                meta = build_chat_meta(
                    model_name=effective_model,
                    profile_name=profile_name,
                    route=route,
                    tools=[],
                    run_id=run["run_id"],
                    temporal=temporal,
                    web_plan=web_plan,
                    persona=persona_meta,
                    identity_guard=identity_guard,
                    provenance_guard=provenance_guard,
                    cached=True,
                )
                _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": cached, "meta": meta})
                _record_agent_os_monitoring(
                    agent_id=_effective_agent_id,
                    run_id=run["run_id"],
                    route=route,
                    model_name=effective_model,
                    ok=True,
                    duration_ms=int((_time.monotonic() - _agent_start) * 1000),
                    streaming=True,
                    num_ctx=num_ctx,
                    selected_tools=selected,
                )
                _emit_agent_os_event(
                    event_type="agent.run.completed",
                    source_agent_id=_effective_agent_id,
                    payload={
                        "run_id": run["run_id"],
                        "profile_name": profile_name,
                        "route": route,
                        "ok": True,
                        "model_used": effective_model,
                        "duration_ms": int((_time.monotonic() - _agent_start) * 1000),
                        "session_id": str(session_id or ""),
                        "streaming": True,
                    },
                )
                # Стримим кэшированный ответ по токенам (выглядит естественно)
                for token_event in iter_text_stream_events(cached):
                    yield token_event
                yield build_stream_done_event(full_text=cached, meta=meta, timeline=timeline)
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
            persona_meta = observe_dialogue(
                dialog_id=run["run_id"],
                session_id=str(session_id or run["run_id"]),
                profile_name=profile_name,
                model_name=effective_model,
                user_input=raw_user_input,
                answer_text=full_text,
                route=route,
                outcome_ok=True,
            )
            meta = build_chat_meta(
                model_name=effective_model,
                profile_name=profile_name,
                route=route,
                tools=selected,
                run_id=run["run_id"],
                temporal=temporal,
                web_plan=web_plan,
                persona=persona_meta,
                identity_guard=identity_guard,
                provenance_guard=provenance_guard,
            )
            _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": full_text, "meta": meta})
            _record_agent_os_monitoring(
                agent_id=_effective_agent_id,
                run_id=run["run_id"],
                route=route,
                model_name=effective_model,
                ok=True,
                duration_ms=int((_time.monotonic() - _agent_start) * 1000),
                streaming=True,
                num_ctx=num_ctx,
                selected_tools=selected,
            )
            _emit_agent_os_event(
                event_type="agent.run.completed",
                source_agent_id=_effective_agent_id,
                payload={
                    "run_id": run["run_id"],
                    "profile_name": profile_name,
                    "route": route,
                    "ok": True,
                    "model_used": effective_model,
                    "duration_ms": int((_time.monotonic() - _agent_start) * 1000),
                    "session_id": str(session_id or ""),
                    "streaming": True,
                },
            )
            yield build_stream_done_event(full_text=full_text, meta=meta, timeline=timeline)
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

            persona_meta = observe_dialogue(
                dialog_id=run["run_id"],
                session_id=str(session_id or run["run_id"]),
                profile_name=profile_name,
                model_name=effective_model,
                user_input=raw_user_input,
                answer_text=full_text,
                route=route,
                outcome_ok=True,
            )
            meta = build_chat_meta(
                model_name=effective_model,
                profile_name=profile_name,
                route=route,
                tools=selected,
                run_id=run["run_id"],
                temporal=temporal,
                web_plan=web_plan,
                persona=persona_meta,
                identity_guard=identity_guard,
                provenance_guard=provenance_guard,
            )
            _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": full_text, "meta": meta})
            _record_agent_os_monitoring(
                agent_id=_effective_agent_id,
                run_id=run["run_id"],
                route=route,
                model_name=effective_model,
                ok=True,
                duration_ms=int((_time.monotonic() - _agent_start) * 1000),
                streaming=True,
                num_ctx=num_ctx,
                selected_tools=selected,
            )
            _emit_agent_os_event(
                event_type="agent.run.completed",
                source_agent_id=_effective_agent_id,
                payload={
                    "run_id": run["run_id"],
                    "profile_name": profile_name,
                    "route": route,
                    "ok": True,
                    "model_used": effective_model,
                    "duration_ms": int((_time.monotonic() - _agent_start) * 1000),
                    "session_id": str(session_id or ""),
                    "streaming": True,
                },
            )
            yield build_stream_done_event(full_text=full_text, meta=meta, timeline=timeline)
    except Exception as exc:
        _HISTORY.finish_run(run["run_id"], {"ok": False, "error": str(exc)})
        _record_agent_os_monitoring(
            agent_id=_effective_agent_id,
            run_id=run["run_id"],
            route=locals().get("route", ""),
            model_name=locals().get("effective_model", model_name),
            ok=False,
            duration_ms=int((_time.monotonic() - _agent_start) * 1000),
            streaming=True,
            num_ctx=num_ctx,
            selected_tools=locals().get("selected", []),
        )
        _emit_agent_os_event(
            event_type="agent.run.completed",
            source_agent_id=_effective_agent_id,
            payload={
                "run_id": run["run_id"],
                "profile_name": profile_name,
                "route": locals().get("route", ""),
                "ok": False,
                "model_used": locals().get("effective_model", model_name),
                "duration_ms": int((_time.monotonic() - _agent_start) * 1000),
                "error": str(exc)[:500],
                "session_id": str(session_id or ""),
                "streaming": True,
            },
        )
        yield {"token": "", "done": True, "error": str(exc), "full_text": ""}
