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
from app.application.chat.prompt_builder import (
    compose_human_style_rules as _compose_human_style_rules,
    wants_explicit_datetime_answer as _wants_explicit_datetime_answer,
    build_runtime_datetime_context as _build_runtime_datetime_context,
)
from app.infrastructure.search.web_search import (
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

def _clean_query(query):
    """Facade — delegates to infrastructure.search.web_search."""
    return _infra_clean_query(query)


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
    """Facade — delegates to infrastructure.search.web_search."""
    return _infra_is_strict_web_only_query(user_input)


def _get_web_search_result(tool_results):
    """Facade — delegates to infrastructure.search.web_search."""
    return _infra_get_web_search_result(tool_results)



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
# WEB SEARCH — delegated to infrastructure/search/web_search.py
# Legacy facades kept for compatibility with _collect_context_legacy.
# ═══════════════════════════════════════════════════════════════

def _build_single_web_subquery_context(subquery):
    """Facade — delegates to infrastructure.search.web_search."""
    from app.infrastructure.search.web_search import build_single_web_subquery_context
    return build_single_web_subquery_context(subquery)


def _do_web_search_legacy(query, timeline, tool_results):
    """Facade — delegates to infrastructure.search.web_search."""
    return _infra_do_web_search_legacy(query, timeline, tool_results, tl=_tl)


def _do_temporal_web_search_legacy(query, timeline, tool_results, temporal=None):
    """Facade — delegates to infrastructure.search.web_search."""
    return _infra_do_temporal_web_search_legacy(query, timeline, tool_results, temporal=temporal, tl=_tl)


def _do_web_search(query, timeline, tool_results, web_plan=None):
    """Facade — delegates to infrastructure.search.web_search."""
    return _infra_do_web_search(query, timeline, tool_results, web_plan=web_plan, tl=_tl)


def _do_temporal_web_search(query, timeline, tool_results, temporal=None, web_plan=None):
    """Facade — delegates to infrastructure.search.web_search."""
    return _infra_do_temporal_web_search(query, timeline, tool_results, temporal=temporal, web_plan=web_plan, tl=_tl)


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
    _skill_flags = {"web_search": use_web_search, "python_exec": use_python_exec, "image_gen": use_image_gen, "file_gen": use_file_gen, "http_api": use_http_api, "sql": use_sql, "screenshot": use_screenshot, "encrypt": use_encrypt, "archiver": use_archiver, "converter": use_converter, "regex": use_regex, "translator": use_translator, "csv_analysis": use_csv, "webhook": use_webhook, "plugins": use_plugins}
    _disabled_skills = {k for k, v in _skill_flags.items() if not v}
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
        plan = planner.plan(planner_input)
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = plan.get("route", "chat")
        temporal = plan.get("temporal", {})
        web_plan = plan.get("web_plan", {"is_multi_intent": False, "subqueries": []})
        effective_model = pick_model_for_route(route, model_name)
        selected = [t for t in plan.get("tools", []) if not (t == "memory_search" and not use_memory) and not (t == "library_context" and not use_library) and not (t == "web_search" and not use_web_search)]
        if temporal.get("requires_web") and use_web_search and "web_search" not in selected:
            selected.append("web_search")
        strict_web_only = route == "research" and temporal.get("mode") == "hard" and temporal.get("freshness_sensitive")
        if strict_web_only:
            selected = [t for t in selected if t != "memory_search"]
        if is_memory_command(planner_input):
            selected = [t for t in selected if t != "memory_search"]

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
        task_context = f"Маршрут: {route}. Инструменты: {', '.join(selected) if selected else 'нет дополнительных инструментов'}."
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
            "meta": {
                "model_name": effective_model,
                "profile_name": profile_name,
                "route": route,
                "tools": selected,
                "run_id": run["run_id"],
                "persona": persona_meta,
                "temporal": temporal,
                "web_plan": web_plan,
                "identity_guard": identity_guard if identity_guard.get("changed") else None,
                "provenance_guard": provenance_guard if provenance_guard.get("changed") else None,
            },
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
    _skill_flags = {"web_search": use_web_search, "python_exec": use_python_exec, "image_gen": use_image_gen, "file_gen": use_file_gen, "http_api": use_http_api, "sql": use_sql, "screenshot": use_screenshot, "encrypt": use_encrypt, "archiver": use_archiver, "converter": use_converter, "regex": use_regex, "translator": use_translator, "csv_analysis": use_csv, "webhook": use_webhook, "plugins": use_plugins}
    _disabled_skills = {k for k, v in _skill_flags.items() if not v}
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

        plan = planner.plan(planner_input)
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = plan.get("route", "chat")
        temporal = plan.get("temporal", {})
        web_plan = plan.get("web_plan", {"is_multi_intent": False, "subqueries": []})
        selected = [t for t in plan.get("tools", []) if not (t == "memory_search" and not use_memory) and not (t == "library_context" and not use_library) and not (t == "web_search" and not use_web_search)]
        if temporal.get("requires_web") and use_web_search and "web_search" not in selected:
            selected.append("web_search")
        strict_web_only = route == "research" and temporal.get("mode") == "hard" and temporal.get("freshness_sensitive")
        if strict_web_only:
            selected = [t for t in selected if t != "memory_search"]
        if is_memory_command(planner_input):
            selected = [t for t in selected if t != "memory_search"]

        # ═══ АВТО-ВЫБОР МОДЕЛИ (тихо, без UI) ═══
        effective_model = pick_model_for_route(route, model_name)
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
                meta = {
                    "model_name": effective_model,
                    "profile_name": profile_name,
                    "route": route,
                    "tools": [],
                    "run_id": run["run_id"],
                    "cached": True,
                    "temporal": temporal,
                    "web_plan": web_plan,
                    "identity_guard": identity_guard if identity_guard.get("changed") else None,
                    "provenance_guard": provenance_guard if provenance_guard.get("changed") else None,
                }
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
                meta["persona"] = persona_meta
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
                words = cached.split(" ")
                for i, word in enumerate(words):
                    token = word if i == 0 else " " + word
                    yield {"token": token, "done": False}
                yield {"token": "", "done": True, "full_text": cached, "meta": meta, "timeline": timeline}
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
        task_context = f"Маршрут: {route}. Инструменты: {', '.join(selected) if selected else 'нет дополнительных инструментов'}."
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
            meta = {
                "model_name": effective_model,
                "profile_name": profile_name,
                "route": route,
                "tools": selected,
                "run_id": run["run_id"],
                "persona": persona_meta,
                "temporal": temporal,
                "web_plan": web_plan,
                "identity_guard": identity_guard if identity_guard.get("changed") else None,
                "provenance_guard": provenance_guard if provenance_guard.get("changed") else None,
            }
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
            yield {"token": "", "done": True, "full_text": full_text, "meta": meta, "timeline": timeline}
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
            meta = {
                "model_name": effective_model,
                "profile_name": profile_name,
                "route": route,
                "tools": selected,
                "run_id": run["run_id"],
                "persona": persona_meta,
                "temporal": temporal,
                "web_plan": web_plan,
                "identity_guard": identity_guard if identity_guard.get("changed") else None,
                "provenance_guard": provenance_guard if provenance_guard.get("changed") else None,
            }
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
            yield {"token": "", "done": True, "full_text": full_text, "meta": meta, "timeline": timeline}
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
