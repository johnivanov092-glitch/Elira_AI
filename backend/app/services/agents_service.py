"""
agents_service.py v8

Улучшения v8:
  • Авто-выбор модели под задачу (route → лучшая модель)
  • Кэширование ответов (SQLite, TTL 2 часа)
  • Умная обрезка истории (релевантные сообщения, не просто последние N)
  • Детальные фазы стриминга
"""
from __future__ import annotations

import re
import logging
from typing import Any, Generator

from app.services.chat_service import run_chat, run_chat_stream
from app.services.planner_v2_service import PlannerV2Service
from app.services.reflection_loop_service import run_reflection_loop
from app.services.run_history_service import RunHistoryService
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
    if (is_news or is_price or is_weather):
        if not any(y in q for y in ["2024", "2025", "2026"]):
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


def _strip_frontend_project_context(user_input: str) -> str:
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
            title = title or "Документ Jarvis"

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

    # ─── 🔌 Плагины ───
    plugin_triggers = ["список плагинов", "покажи плагины", "plugins list"]
    if "plugins" not in disabled and any(t in ql for t in plugin_triggers):
        try:
            from app.services.plugin_system import list_plugins
            result = list_plugins()
            plugins = result.get("plugins", [])
            if plugins:
                lines = [f"🔌 Плагины ({len(plugins)}):"]
                for p in plugins:
                    lines.append(f"  • {p['name']} — {p.get('description','')}")
                parts.append("\n".join(lines))
            else:
                parts.append("🔌 Плагинов нет. Положи .py файлы в data/plugins/")
        except Exception as e:
            parts.append(f"SKILL_ERROR:🔌 Плагины: {e}")

    run_plugin_triggers = ["запусти плагин", "выполни плагин", "run plugin"]
    if "plugins" not in disabled and any(t in ql for t in run_plugin_triggers):
        try:
            from app.services.plugin_system import run_plugin
            name_match = _re.search(r"плагин\s+(\S+)", user_input, _re.IGNORECASE)
            if not name_match:
                name_match = _re.search(r"plugin\s+(\S+)", user_input, _re.IGNORECASE)
            if name_match:
                result = run_plugin(name_match.group(1))
                parts.append(f"🔌 Плагин {name_match.group(1)}: {json.dumps(result, ensure_ascii=False)[:2000]}")
        except Exception as e:
            parts.append(f"SKILL_ERROR:🔌 Плагин: {e}")

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
            from pathlib import Path as _P
            gen_dir = _P("data/generated")
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
        "ОБЯЗАТЕЛЬНО используй данные выше для ответа. "
        "Если в данных есть конкретные цифры, ссылки или факты — приведи их. "
        "Не говори что данных нет, если они есть выше."
    )


# Хранилище для вложений (картинки, файлы) которые добавляются ПОСЛЕ ответа LLM
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


def _do_web_search(query, timeline, tool_results):
    """Глубокий поиск: ddgs → берёт топ-3 URL → заходит и вытаскивает контент."""
    search_query = _clean_query(query)
    errors = []

    # Шаг 1: получаем URL через ddgs
    search_results = []
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
                search_results.append({
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": r.get("body", ""),
                })
    except Exception as e:
        errors.append("ddgs: " + str(e))

    if not search_results:
        # HTML fallback
        try:
            import requests
            from bs4 import BeautifulSoup

            resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": search_query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            for item in soup.select(".result, .web-result"):
                a = item.select_one("a.result__a")
                snip = item.select_one(".result__snippet")
                if a and a.get("href"):
                    href = a["href"]
                    if "uddg=" in href:
                        from urllib.parse import parse_qs, urlparse, unquote
                        href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
                    if href.startswith("http"):
                        search_results.append({
                            "title": a.get_text(strip=True),
                            "url": href,
                            "snippet": snip.get_text(strip=True) if snip else "",
                        })
                if len(search_results) >= 8:
                    break
        except Exception as e:
            errors.append("html: " + str(e))

    if not search_results:
        err_msg = " | ".join(errors) or "Нет результатов"
        _tl(timeline, "tool_web", "Веб-поиск", "error", err_msg)
        tool_results.append({"tool": "web_search", "result": {"count": 0, "errors": errors}})
        return "[Поиск не дал результатов: " + err_msg + "]"

    # Шаг 2: заходим на топ-3 сайта и вытаскиваем контент
    deep_content = []
    fetched_count = 0
    skip_domains = ["youtube.com", "youtu.be", "facebook.com", "instagram.com",
                    "tiktok.com", "twitter.com", "x.com", "vk.com", "t.me",
                    "pinterest.com", "wikipedia.org"]  # wiki даёт слишком общую инфу
    for item in search_results[:5]:  # Пробуем 5, берём 3
        url = item["url"]
        if any(d in url for d in skip_domains):
            continue
        page_text = _fetch_page_text(url, max_chars=3000)
        if page_text and len(page_text) > 100:
            deep_content.append(
                "--- " + item["title"] + " ---\n"
                "URL: " + url + "\n"
                + page_text
            )
            fetched_count += 1
        if fetched_count >= 3:
            break

    # Шаг 3: формируем контекст
    tool_results.append({"tool": "web_search", "result": {
        "query": search_query,
        "found": len(search_results),
        "fetched_pages": fetched_count,
    }})
    _tl(timeline, "tool_web", "Веб-поиск", "done",
        str(len(search_results)) + " найдено, " + str(fetched_count) + " страниц загружено")

    parts = []

    # Глубокий контент (со страниц)
    if deep_content:
        parts.append("Содержимое веб-страниц:\n\n" + "\n\n".join(deep_content))

    # Сниппеты остальных результатов
    remaining = search_results[fetched_count:6] if fetched_count < len(search_results) else []
    if remaining:
        snippet_lines = [("- " + s["title"] + ": " + s["snippet"] + " (" + s["url"] + ")") for s in remaining]
        parts.append("Другие результаты поиска:\n" + "\n".join(snippet_lines))

    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# КОНТЕКСТ
# ═══════════════════════════════════════════════════════════════

def _collect_context(*, profile_name, user_input, tools, tool_results, timeline, use_reflection=False):
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
                web_ctx = _do_web_search(user_input, timeline, tool_results)
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
# run_agent
# ═══════════════════════════════════════════════════════════════

def run_agent(*, model_name, profile_name, user_input, use_memory=True, use_library=True, use_reflection=False, history=None, num_ctx=8192, use_web_search=True, use_python_exec=True, use_image_gen=True, use_file_gen=True, use_http_api=True, use_sql=True, use_screenshot=True, use_encrypt=True, use_archiver=True, use_converter=True, use_regex=True, use_translator=True, use_csv=True, use_webhook=True, use_plugins=True):
    history = _trim_history(history or [])
    _skill_flags = {"web_search": use_web_search, "python_exec": use_python_exec, "image_gen": use_image_gen, "file_gen": use_file_gen, "http_api": use_http_api, "sql": use_sql, "screenshot": use_screenshot, "encrypt": use_encrypt, "archiver": use_archiver, "converter": use_converter, "regex": use_regex, "translator": use_translator, "csv_analysis": use_csv, "webhook": use_webhook, "plugins": use_plugins}
    _disabled_skills = {k for k, v in _skill_flags.items() if not v}
    timeline, tool_results = [], []
    planner = PlannerV2Service()
    raw_user_input = user_input
    planner_input = _strip_frontend_project_context(user_input)
    run = _HISTORY.start_run(raw_user_input)
    try:
        plan = planner.plan(planner_input)
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = plan.get("route", "chat")
        effective_model = pick_model_for_route(route, model_name)
        selected = [t for t in plan.get("tools", []) if not (t == "memory_search" and not use_memory) and not (t == "library_context" and not use_library) and not (t == "web_search" and not use_web_search)]
        strict_web_only = route == "research" and _is_strict_web_only_query(planner_input)
        if strict_web_only:
            selected = [t for t in selected if t != "memory_search"]
        strict_web_only = route == "research" and _is_strict_web_only_query(planner_input)
        if strict_web_only:
            selected = [t for t in selected if t != "memory_search"]

        # Умная память: извлекаем факты из сообщения
        try:
            saved = extract_and_save(planner_input)
            if saved:
                _tl(timeline, "memory_save", "Память", "done", "Сохранено: " + str(len(saved)))
        except Exception:
            pass

        ctx = _collect_context(profile_name=profile_name, user_input=planner_input, tools=selected, tool_results=tool_results, timeline=timeline, use_reflection=use_reflection)

        # Умная память + RAG: добавляем релевантные воспоминания
        try:
            mem_ctx = get_relevant_context(planner_input, max_items=5)
            if _HAS_RAG:
                rag_ctx = get_rag_context(planner_input, max_items=3)
                if rag_ctx:
                    mem_ctx = (mem_ctx + "\n\n" + rag_ctx) if mem_ctx else rag_ctx
            if mem_ctx:
                ctx = mem_ctx + "\n\n" + ctx if ctx else mem_ctx
                _tl(timeline, "memory_recall", "Память", "done", "Найдены воспоминания")
        except Exception:
            pass

        prompt = _build_prompt(raw_user_input, ctx, disabled_skills=_disabled_skills)
        draft = run_chat(model_name=effective_model, profile_name=profile_name, user_input=prompt, history=history, num_ctx=num_ctx)
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

        result = {"ok": True, "answer": answer, "timeline": timeline, "tool_results": tool_results, "meta": {"model_name": effective_model, "profile_name": profile_name, "route": route, "tools": selected, "run_id": run["run_id"]}}
        _HISTORY.finish_run(run["run_id"], result)
        return result
    except Exception as exc:
        err = {"ok": False, "answer": "", "timeline": timeline + [{"step": "error", "title": "Ошибка", "status": "error", "detail": str(exc)}], "tool_results": tool_results, "meta": {"error": str(exc), "run_id": run["run_id"]}}
        _HISTORY.finish_run(run["run_id"], err)
        return err


# ═══════════════════════════════════════════════════════════════
# run_agent_stream
# ═══════════════════════════════════════════════════════════════

def run_agent_stream(*, model_name, profile_name, user_input, use_memory=True, use_library=True, use_reflection=False, history=None, num_ctx=8192, use_web_search=True, use_python_exec=True, use_image_gen=True, use_file_gen=True, use_http_api=True, use_sql=True, use_screenshot=True, use_encrypt=True, use_archiver=True, use_converter=True, use_regex=True, use_translator=True, use_csv=True, use_webhook=True, use_plugins=True):
    history = _trim_history(history or [])
    _skill_flags = {"web_search": use_web_search, "python_exec": use_python_exec, "image_gen": use_image_gen, "file_gen": use_file_gen, "http_api": use_http_api, "sql": use_sql, "screenshot": use_screenshot, "encrypt": use_encrypt, "archiver": use_archiver, "converter": use_converter, "regex": use_regex, "translator": use_translator, "csv_analysis": use_csv, "webhook": use_webhook, "plugins": use_plugins}
    _disabled_skills = {k for k, v in _skill_flags.items() if not v}
    timeline, tool_results = [], []
    planner = PlannerV2Service()
    raw_user_input = user_input
    planner_input = _strip_frontend_project_context(user_input)
    run = _HISTORY.start_run(raw_user_input)
    try:
        yield {"token": "", "done": False, "phase": "planning", "message": "Думаю..."}

        plan = planner.plan(planner_input)
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = plan.get("route", "chat")
        selected = [t for t in plan.get("tools", []) if not (t == "memory_search" and not use_memory) and not (t == "library_context" and not use_library) and not (t == "web_search" and not use_web_search)]

        # ═══ АВТО-ВЫБОР МОДЕЛИ (тихо, без UI) ═══
        effective_model = pick_model_for_route(route, model_name)
        if effective_model != model_name:
            _tl(timeline, "auto_model", "Авто-модель", "ok", f"{model_name} → {effective_model} (route={route})")

        # ═══ КЭШИРОВАНИЕ ═══
        if should_cache(planner_input, route) and not history:
            cached = get_cached(planner_input, effective_model, profile_name)
            if cached:
                _tl(timeline, "cache_hit", "Кэш", "ok", "Ответ из кэша")
                meta = {"model_name": effective_model, "profile_name": profile_name, "route": route, "tools": [], "run_id": run["run_id"], "cached": True}
                _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": cached, "meta": meta})
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

        ctx = _collect_context(profile_name=profile_name, user_input=planner_input, tools=selected, tool_results=tool_results, timeline=timeline, use_reflection=use_reflection)

        # Умная память + RAG
        mem_count = 0
        try:
            mem_ctx = get_relevant_context(planner_input, max_items=5)
            if mem_ctx:
                mem_count = mem_ctx.count("\n- ")
            if _HAS_RAG:
                rag_ctx = get_rag_context(planner_input, max_items=3)
                if rag_ctx:
                    mem_ctx = (mem_ctx + "\n\n" + rag_ctx) if mem_ctx else rag_ctx
            if mem_ctx:
                ctx = mem_ctx + "\n\n" + ctx if ctx else mem_ctx
        except Exception:
            pass

        yield {"token": "", "done": False, "phase": "thinking", "message": "Пишу ответ..."}

        prompt = _build_prompt(raw_user_input, ctx, disabled_skills=_disabled_skills)
        full_text = ""
        for token in run_chat_stream(model_name=effective_model, profile_name=profile_name, user_input=prompt, history=history, num_ctx=num_ctx):
            full_text += token
            yield {"token": token, "done": False}

        # Сохраняем в кэш если подходит
        if should_cache(planner_input, route) and full_text.strip():
            try:
                set_cached(planner_input, effective_model, profile_name, full_text)
            except Exception:
                pass

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
            meta = {"model_name": effective_model, "profile_name": profile_name, "route": route, "tools": selected, "run_id": run["run_id"]}
            _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": full_text, "meta": meta})
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

            # Кэшируем после всех пост-обработок
            if should_cache(planner_input, route) and full_text.strip():
                try:
                    set_cached(planner_input, effective_model, profile_name, full_text)
                except Exception:
                    pass

            meta = {"model_name": effective_model, "profile_name": profile_name, "route": route, "tools": selected, "run_id": run["run_id"]}
            _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": full_text, "meta": meta})
            yield {"token": "", "done": True, "full_text": full_text, "meta": meta, "timeline": timeline}
    except Exception as exc:
        _HISTORY.finish_run(run["run_id"], {"ok": False, "error": str(exc)})
        yield {"token": "", "done": True, "error": str(exc), "full_text": ""}
