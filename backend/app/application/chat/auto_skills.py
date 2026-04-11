"""
Auto-skills detection and execution.

Keyword-based skill dispatcher that detects user intent from input text
and runs matching skills (HTTP/API, SQL, screenshots, image generation,
translation, encryption, archiving, file conversion, regex, CSV analysis,
webhooks, plugins, git, GPU status, file listing).
"""
from __future__ import annotations

import json
import re as _re
from typing import Any


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



def maybe_generate_files(user_input: str, llm_answer: str, enabled: bool = True) -> str:
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


def run_auto_skills(user_input: str, disabled: set | None = None) -> str:
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
