"""Auto-skill detection, prompt assembly, and post-generation file helpers.

Extracted from agents_service.py — these modules share _pending_attachments
state and operate together in the chat request pipeline.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.application.chat.prompt_builder import build_runtime_datetime_context
from app.application.skills.skills_extra import (
    analyze_csv,
    convert_file,
    create_zip,
    decrypt_text,
    encrypt_text,
    extract_zip,
    list_webhooks,
    test_regex,
    translate_text,
)
from app.application.skills.skills_service import (
    describe_db,
    generate_excel,
    generate_word,
    http_request,
    list_databases,
    run_sql,
    screenshot_url,
)
from app.core.config import GENERATED_DIR
from app.infrastructure.integrations.image_gen import generate_image, get_status
from app.infrastructure.plugins.plugin_system import (
    fire_hook,
    list_plugins,
    run_plugin,
    run_triggered,
)
from app.infrastructure.runtime.python_runner import execute_python
from app.infrastructure.vcs.git_service import format_git_context, git_diff, git_log

_build_runtime_datetime_context = build_runtime_datetime_context

def _tl(timeline: list, step: str, title: str, status: str, detail: str) -> None:
    timeline.append({"step": step, "title": title, "status": status, "detail": detail})


_EXEC_TRIGGERS = ["запусти", "посчитай", "вычисли", "выполни", "рассчитай", "run", "execute", "calculate", "compute"]


def _maybe_auto_exec_python(user_input, answer, timeline, enabled: bool = True):
    """Если пользователь просил выполнить и ответ содержит Python — запускаем."""
    if not enabled:
        return answer
    ql = user_input.lower()
    if not any(t in ql for t in _EXEC_TRIGGERS):
        return answer
    match = re.search(r"```python\n([\s\S]*?)```", answer)
    if not match:
        return answer
    code = match.group(1).strip()
    if not code or len(code) < 10:
        return answer
    try:
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


def _try_generate_word(ql: str, llm_answer: str) -> str:
    """Generate a Word document from the LLM answer if the user requested one.

    Returns the appendix string (e.g. a download link line) or "".
    """
    if not (any(t in ql for t in _FILE_TRIGGERS_WORD) and len(llm_answer) > 50):
        return ""
    try:
        lines = llm_answer.strip().split("\n")
        title = ""
        for line in lines:
            clean = line.strip().strip("#").strip("*").strip()
            if clean and len(clean) > 3:
                title = clean[:80]
                break
        title = title or "Документ Elira"
        result = generate_word(title, llm_answer)
        if result.get("ok"):
            fname = result.get("filename", "")
            dl = result.get("download_url", "")
            return f"\n\n📄 **Word документ создан:** [{fname}]({dl})"
    except Exception as e:
        return f"\n\n⚠️ Word ошибка: {e}"
    return ""


def _try_generate_excel(ql: str, llm_answer: str) -> str:
    """Generate an Excel file from the LLM answer if the user requested one.

    Parses markdown tables when present; falls back to a plain text export.
    Returns the appendix string (e.g. a download link line) or "".
    """
    if not (any(t in ql for t in _FILE_TRIGGERS_EXCEL) and len(llm_answer) > 30):
        return ""
    try:
        table_pattern = re.findall(r'\|(.+)\|', llm_answer)
        if table_pattern and len(table_pattern) >= 2:
            rows: list[list[str]] = []
            headers: list[str] = []
            for row_str in table_pattern:
                cells = [c.strip() for c in row_str.split("|") if c.strip()]
                if cells and all(set(c) <= {'-', ':', ' '} for c in cells):
                    continue  # skip separator rows
                if not headers:
                    headers = cells
                else:
                    rows.append(cells)
            if headers and rows:
                result = generate_excel("Данные", rows, headers)
                if result.get("ok"):
                    fname = result.get("filename", "")
                    dl = result.get("download_url", "")
                    return f"\n\n📊 **Excel файл создан:** [{fname}]({dl})"
        else:
            lines_data = [
                [line.strip()]
                for line in llm_answer.split("\n")
                if line.strip() and not line.strip().startswith(("#", "---"))
            ]
            if lines_data:
                result = generate_excel("Экспорт", lines_data, ["Содержимое"])
                if result.get("ok"):
                    fname = result.get("filename", "")
                    dl = result.get("download_url", "")
                    return f"\n\n📊 **Excel файл создан:** [{fname}]({dl})"
    except Exception as e:
        return f"\n\n⚠️ Excel ошибка: {e}"
    return ""


def _maybe_generate_files(user_input: str, llm_answer: str, enabled: bool = True) -> str:
    """После ответа LLM: если пользователь хотел Word/Excel — создаём файлы из ответа."""
    if not enabled:
        return ""
    ql = user_input.lower()
    return _try_generate_word(ql, llm_answer) + _try_generate_excel(ql, llm_answer)



def _handle_http_skill(url_match, ql: str, disabled: set, parts: list) -> None:
    _TRIGGERS = ["запрос к api", "api запрос", "fetch", "http запрос", "вызови api",
                 "get запрос", "post запрос", "покажи сайт", "загрузи url", "открой ссылку"]
    if "http_api" in disabled or not url_match or not any(t in ql for t in _TRIGGERS):
        return
    try:
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


def _handle_sql_skill(user_input: str, ql: str, disabled: set, parts: list) -> None:
    _TRIGGERS = ["покажи таблиц", "запрос к базе", "sql запрос", "база данных", "покажи базу",
                 "select ", "покажи записи", "покажи данные из"]
    if "sql" in disabled or not any(t in ql for t in _TRIGGERS):
        return
    try:
        sql_match = re.search(r"(SELECT\s+.+)", user_input, re.IGNORECASE)
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


def _handle_screenshot_skill(url_match, ql: str, disabled: set, parts: list) -> None:
    _TRIGGERS = ["скриншот", "screenshot", "покажи как выглядит", "сделай снимок"]
    if "screenshot" in disabled or not url_match or not any(t in ql for t in _TRIGGERS):
        return
    try:
        result = screenshot_url(url_match.group(1))
        if result.get("ok"):
            parts.append(f"IMAGE_GENERATED:{result.get('view_url','')}:{result.get('filename','')}:Скриншот {result.get('title','')}")
        else:
            parts.append(f"SKILL_ERROR:🖼 Скриншот: {result.get('error')}")
    except Exception as e:
        parts.append(f"SKILL_ERROR:🖼 Скриншот: {e}")


def _run_network_skills(
    user_input: str, ql: str, url_match, disabled: set, parts: list
) -> None:
    """HTTP/API, SQL, screenshot."""
    _handle_http_skill(url_match, ql, disabled, parts)
    _handle_sql_skill(user_input, ql, disabled, parts)
    _handle_screenshot_skill(url_match, ql, disabled, parts)


def _run_media_skills(user_input: str, ql: str, disabled: set, parts: list) -> None:
    """Image generation and Word/Excel file hints."""
    # ─── 🎨 Генерация картинок ───
    img_triggers = ["нарисуй", "нарисуй мне", "сгенерируй картинк", "сгенерируй изображен",
                    "создай картинк", "создай изображен", "generate image", "draw me",
                    "сделай картинк", "покажи картинк", "нарисовать"]
    if "image_gen" not in disabled and any(t in ql for t in img_triggers):
        try:
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


def _handle_translate_skill(user_input: str, ql: str, disabled: set, parts: list) -> None:
    _TRIGGERS = ["переведи на ", "переведи в ", "translate to ", "перевод на ", "переведи текст"]
    if "translator" in disabled:
        return
    for t in _TRIGGERS:
        if t in ql:
            try:
                after = user_input[ql.find(t) + len(t):].strip()
                lang_text = after.split(":", 1) if ":" in after else after.split(" ", 1)
                target_lang = lang_text[0].strip() if lang_text else "english"
                text_to_translate = lang_text[1].strip() if len(lang_text) > 1 else ""
                if text_to_translate and len(text_to_translate) > 2:
                    result = translate_text(text_to_translate, target_lang)
                    if result.get("ok"):
                        parts.append(f"Перевод ({target_lang}):\n{result.get('translated', '')}")
            except Exception as e:
                parts.append(f"SKILL_ERROR:🌍 Перевод: {e}")
            break


def _handle_encrypt_skill(user_input: str, ql: str, disabled: set, parts: list) -> None:
    if "encrypt" in disabled:
        return
    if any(t in ql for t in ["зашифруй", "шифрование", "encrypt"]):
        try:
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
    if any(t in ql for t in ["расшифруй", "дешифруй", "decrypt"]):
        try:
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


def _run_text_skills(user_input: str, ql: str, disabled: set, parts: list) -> None:
    """Translate, encrypt, decrypt."""
    _handle_translate_skill(user_input, ql, disabled, parts)
    _handle_encrypt_skill(user_input, ql, disabled, parts)


def _handle_zip_skill(user_input: str, ql: str, disabled: set, parts: list) -> None:
    triggers = ["запакуй", "архивируй", "создай архив", "создай zip", "сделай zip"]
    if "archiver" in disabled or not any(t in ql for t in triggers):
        return
    try:
        path = next((user_input[ql.find(t) + len(t):].strip().strip(":").strip() for t in triggers if ql.find(t) >= 0), user_input)
        if path:
            result = create_zip(path)
            parts.append(f"FILE_GENERATED:zip:{result.get('download_url','')}:{result.get('filename','')}" if result.get("ok") else f"SKILL_ERROR:📦 Архив: {result.get('error')}")
    except Exception as e:
        parts.append(f"SKILL_ERROR:📦 Архив: {e}")


def _handle_unzip_skill(user_input: str, ql: str, disabled: set, parts: list) -> None:
    triggers = ["распакуй", "разархивируй", "извлеки архив"]
    if "archiver" in disabled or not any(t in ql for t in triggers):
        return
    try:
        path = next((user_input[ql.find(t) + len(t):].strip().strip(":").strip() for t in triggers if ql.find(t) >= 0), user_input)
        if path:
            result = extract_zip(path)
            if result.get("ok"):
                parts.append(f"📦 Распаковано в {result.get('dest','')}: {result.get('count',0)} файлов")
    except Exception as e:
        parts.append(f"SKILL_ERROR:📦 Распаковка: {e}")


def _handle_convert_skill(user_input: str, ql: str, disabled: set, parts: list) -> None:
    triggers = ["конвертируй", "преобразуй", "конвертировать", "convert "]
    if "converter" in disabled or not any(t in ql for t in triggers):
        return
    try:
        # Парсим: "конвертируй data.csv в xlsx"
        match = re.search(r"(\S+\.\w+)\s+в\s+(\w+)", user_input, re.IGNORECASE) or \
                re.search(r"(\S+\.\w+)\s+to\s+(\w+)", user_input, re.IGNORECASE)
        if match:
            result = convert_file(match.group(1), match.group(2))
            parts.append(f"FILE_GENERATED:convert:{result.get('download_url','')}:{result.get('filename','')}" if result.get("ok") else f"SKILL_ERROR:🔄 Конвертация: {result.get('error')}")
    except Exception as e:
        parts.append(f"SKILL_ERROR:🔄 Конвертация: {e}")


def _handle_regex_skill(user_input: str, ql: str, disabled: set, parts: list) -> None:
    triggers = ["проверь regex", "тест regex", "regex тест", "test regex", "регулярка", "регулярное выражение"]
    if "regex" in disabled or not any(t in ql for t in triggers):
        return
    try:
        # Парсим: "проверь regex \d+ на строке abc123def"
        match = re.search(r"regex[:\s]+(.+?)\s+(?:на строке|на тексте|on|text)[:\s]+(.+)", user_input, re.IGNORECASE) or \
                re.search(r"регуляр\S*[:\s]+(.+?)\s+(?:на|в|for)[:\s]+(.+)", user_input, re.IGNORECASE)
        if match:
            result = test_regex(match.group(1).strip(), match.group(2).strip())
            if result.get("ok"):
                matches = result.get("matches", [])
                parts.append(f"📐 Regex `{match.group(1).strip()}`: {result.get('count',0)} совпадений\n" +
                             "\n".join(f"  • `{m['match']}` (позиция {m['start']}-{m['end']})" for m in matches[:10]))
    except Exception as e:
        parts.append(f"SKILL_ERROR:📐 Regex: {e}")


def _handle_csv_skill(user_input: str, ql: str, disabled: set, parts: list) -> None:
    triggers = ["проанализируй csv", "анализ csv", "статистика csv", "analyze csv", "проанализируй файл", "покажи статистику"]
    if "csv_analysis" in disabled or not any(t in ql for t in triggers):
        return
    try:
        file_match = re.search(r"(\S+\.csv)", user_input, re.IGNORECASE)
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


def _run_file_skills(user_input: str, ql: str, disabled: set, parts: list) -> None:
    """Zip, unzip, convert, regex, CSV analysis."""
    _handle_zip_skill(user_input, ql, disabled, parts)
    _handle_unzip_skill(user_input, ql, disabled, parts)
    _handle_convert_skill(user_input, ql, disabled, parts)
    _handle_regex_skill(user_input, ql, disabled, parts)
    _handle_csv_skill(user_input, ql, disabled, parts)


def _handle_webhook_skill(user_input: str, ql: str, disabled: set, parts: list) -> None:
    _triggers = ["покажи вебхуки", "покажи webhook", "что пришло на webhook", "список вебхуков"]
    if "webhook" in disabled or not any(t in ql for t in _triggers):
        return
    try:
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


def _handle_plugins_skill(user_input: str, ql: str, disabled: set, parts: list) -> None:
    if "plugins" in disabled:
        return
    try:
        _list_triggers = ["список плагинов", "покажи плагины", "plugins list", "мои плагины"]
        if any(t in ql for t in _list_triggers):
            plugins = list_plugins().get("plugins", [])
            if plugins:
                lines = [f"🔌 Плагины ({len(plugins)}):"]
                for p in plugins:
                    st = "✅" if p.get("enabled") else "⛔"
                    lines.append(f"  {st} {p.get('icon','🔌')} {p['name']} v{p.get('version','1.0')} — {p.get('description','')}")
                parts.append("\n".join(lines))
            else:
                parts.append("🔌 Плагинов нет. Положи .py файлы в data/plugins/")

        _run_triggers = ["запусти плагин", "выполни плагин", "run plugin"]
        if any(t in ql for t in _run_triggers):
            nm = re.search(r"плагин\s+(\S+)", user_input, re.IGNORECASE) or re.search(r"plugin\s+(\S+)", user_input, re.IGNORECASE)
            if nm:
                result = run_plugin(nm.group(1), {"text": user_input})
                parts.append(f"🔌 {nm.group(1)}: {json.dumps(result, ensure_ascii=False)[:2000]}")

        for tr in run_triggered(user_input):
            parts.append(f"🔌 [{tr['plugin']}]: {json.dumps(tr, ensure_ascii=False)[:2000]}")
        for hr in fire_hook("on_message", user_input):
            if hr.get("result"):
                parts.append(f"🔌 [{hr['plugin']}]: {hr['result']}")
    except Exception as e:
        parts.append(f"SKILL_ERROR:🔌 Плагины: {e}")


def _run_webhook_plugin_skills(
    user_input: str, ql: str, disabled: set, parts: list
) -> None:
    """Webhook list and plugin execution."""
    _handle_webhook_skill(user_input, ql, disabled, parts)
    _handle_plugins_skill(user_input, ql, disabled, parts)


def _handle_pdf_hints(ql: str, parts: list) -> None:
    if any(t in ql for t in ["конвертируй pdf в word", "pdf в word", "pdf to word", "pdf в docx"]):
        parts.append("SKILL_HINT: Чтобы конвертировать PDF в Word — загрузи PDF через кнопку + и напиши 'конвертируй в word'. PDF будет обработан автоматически через /api/pdf/to-word.")
    if any(t in ql for t in ["извлеки таблицы из pdf", "таблицы из pdf", "pdf таблицы в excel"]):
        parts.append("SKILL_HINT: Чтобы извлечь таблицы из PDF — загрузи PDF через кнопку + и напиши 'извлеки таблицы'. Таблицы будут сохранены в Excel через /api/pdf/tables.")


def _handle_git_skills(ql: str, disabled: set, parts: list) -> None:
    if "git" in disabled:
        return
    if any(t in ql for t in ["git status", "статус git", "что изменилось в git", "покажи git", "git изменения", "ветка git"]):
        try:
            parts.append(format_git_context())
        except Exception as e:
            parts.append("SKILL_ERROR:Git: " + str(e))
    if any(t in ql for t in ["git log", "история коммитов", "последние коммиты", "покажи коммиты"]):
        try:
            r = git_log(limit=10)
            if r.get("ok"):
                rows = ["Git log (" + r["repo"] + "):"] + ["  " + c["hash"] + " - " + c["message"] for c in r.get("commits", [])]
                parts.append("\n".join(rows))
        except Exception as e:
            parts.append("SKILL_ERROR:Git log: " + str(e))
    if any(t in ql for t in ["git diff", "покажи diff", "что я изменил", "изменения в коде"]):
        try:
            r = git_diff()
            if r.get("ok"):
                parts.append("Git diff:\n" + r.get("stat", "") + "\n" + r.get("diff", "")[:3000])
        except Exception as e:
            parts.append("SKILL_ERROR:Git diff: " + str(e))


def _handle_gpu_skill(ql: str, parts: list) -> None:
    if not any(t in ql for t in ["статус gpu", "gpu status", "сколько vram", "видеопамять"]):
        return
    try:
        result = get_status()
        parts.append(
            f"🖥 GPU: {result.get('gpu','?')}\n"
            f"VRAM: {result.get('vram_used_mb',0)} / {result.get('vram_total_mb',0)} MB\n"
            f"Модель загружена: {'да' if result.get('loaded') else 'нет'}"
        )
    except Exception as e:
        parts.append(f"GPU: {e}")


def _handle_files_skill(parts: list) -> None:
    try:
        if GENERATED_DIR.exists():
            files = sorted(GENERATED_DIR.iterdir())[-10:]
            if files:
                lines = ["📊 Последние файлы:"]
                for f in files:
                    lines.append(f"  • [{f.name}]({API_BASE}/api/skills/download/{f.name}) ({f.stat().st_size} байт)")
                parts.append("\n".join(lines))
    except Exception:
        pass


def _run_system_skills(user_input: str, ql: str, disabled: set, parts: list) -> None:
    """PDF hints, git, GPU status, generated files list."""
    _handle_pdf_hints(ql, parts)
    _handle_git_skills(ql, disabled, parts)
    _handle_gpu_skill(ql, parts)
    if any(t in ql for t in ["покажи файлы", "список файлов", "сгенерированные файлы", "мои файлы"]):
        _handle_files_skill(parts)


def _run_auto_skills(user_input: str, disabled: set | None = None) -> str:
    """Auto-detect skills by keyword. disabled — set of disabled skill IDs."""
    disabled = disabled or set()
    ql = user_input.lower()
    parts: list[str] = []
    url_match = re.search(r"(https?://\S+)", user_input)

    _run_network_skills(user_input, ql, url_match, disabled, parts)
    _run_media_skills(user_input, ql, disabled, parts)
    _run_text_skills(user_input, ql, disabled, parts)
    _run_file_skills(user_input, ql, disabled, parts)
    _run_webhook_plugin_skills(user_input, ql, disabled, parts)
    _run_system_skills(user_input, ql, disabled, parts)

    return "\n\n".join(parts)


def _parse_skill_output(skill_results: str) -> tuple[str, list[dict]]:
    """Parse skill result string into clean text and a list of pending attachment records."""
    clean_parts: list[str] = []
    attachments: list[dict] = []
    for line in skill_results.split("\n\n"):
        if line.startswith("IMAGE_GENERATED:"):
            p = line.split(":", 4)
            if len(p) >= 4:
                attachments.append({
                    "type": "image",
                    "view_url": p[1] + ":" + p[2] if "http" in p[1] else p[1],
                    "filename": p[2] if "http" not in p[1] else p[3],
                    "prompt": p[-1],
                })
        elif line.startswith("FILE_GENERATED:"):
            p = line.split(":", 4)
            if len(p) >= 4:
                attachments.append({
                    "type": "file",
                    "file_type": p[1],
                    "download_url": p[2] + ":" + p[3] if "http" in p[2] else p[2],
                    "filename": p[3] if "http" not in p[2] else p[4] if len(p) > 4 else p[3],
                })
        elif line.startswith("SKILL_HINT:"):
            clean_parts.append(line)
        elif line.startswith("SKILL_ERROR:"):
            attachments.append({"type": "error", "message": line[len("SKILL_ERROR:"):]})
        else:
            clean_parts.append(line)
    return "\n\n".join(clean_parts), attachments


def _build_prompt(user_input, context_bundle, mode="default", disabled_skills: set | None = None):
    runtime_context = _build_runtime_datetime_context(user_input)
    skill_results = _run_auto_skills(user_input, disabled=disabled_skills or set())

    _pending_attachments.clear()
    if skill_results:
        skill_results, parsed_attachments = _parse_skill_output(skill_results)
        _pending_attachments.extend(parsed_attachments)

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

