"""
agents_service.py v7

Главное:
  • Глубокий веб-поиск: ddgs → берёт URL → заходит на сайт → вытаскивает текст
  • Скиллы подключены: reflection toggle, python exec
  • Промпт ставит данные ПЕРЕД вопросом
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
from app.services.smart_memory import extract_and_save, get_relevant_context

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
    if not h: return []
    limit = max_pairs * 2
    return list(h[-limit:]) if len(h) > limit else list(h)


def _build_prompt(user_input, context_bundle):
    from datetime import datetime
    days_ru = {"Monday": "понедельник", "Tuesday": "вторник", "Wednesday": "среда", "Thursday": "четверг", "Friday": "пятница", "Saturday": "суббота", "Sunday": "воскресенье"}
    now = datetime.now()
    day_name = days_ru.get(now.strftime("%A"), now.strftime("%A"))
    time_line = f"Сейчас: {now.strftime('%d.%m.%Y, %H:%M')}, {day_name}."

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

def run_agent(*, model_name, profile_name, user_input, use_memory=True, use_library=True, use_reflection=False, history=None):
    history = _trim_history(history or [])
    timeline, tool_results = [], []
    planner = PlannerV2Service()
    run = _HISTORY.start_run(user_input)
    try:
        plan = planner.plan(user_input)
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = plan.get("route", "chat")
        selected = [t for t in plan.get("tools", []) if not (t == "memory_search" and not use_memory) and not (t == "library_context" and not use_library)]

        # Умная память: извлекаем факты из сообщения
        try:
            saved = extract_and_save(user_input)
            if saved:
                _tl(timeline, "memory_save", "Память", "done", "Сохранено: " + str(len(saved)))
        except Exception:
            pass

        ctx = _collect_context(profile_name=profile_name, user_input=user_input, tools=selected, tool_results=tool_results, timeline=timeline, use_reflection=use_reflection)

        # Умная память + RAG: добавляем релевантные воспоминания
        try:
            mem_ctx = get_relevant_context(user_input, max_items=5)
            if _HAS_RAG:
                rag_ctx = get_rag_context(user_input, max_items=3)
                if rag_ctx:
                    mem_ctx = (mem_ctx + "\n\n" + rag_ctx) if mem_ctx else rag_ctx
            if mem_ctx:
                ctx = mem_ctx + "\n\n" + ctx if ctx else mem_ctx
                _tl(timeline, "memory_recall", "Память", "done", "Найдены воспоминания")
        except Exception:
            pass

        prompt = _build_prompt(user_input, ctx)
        draft = run_chat(model_name=model_name, profile_name=profile_name, user_input=prompt, history=history)
        if not draft.get("ok"):
            raise RuntimeError("; ".join(draft.get("warnings", [])) or "LLM failed")
        answer = draft.get("answer", "")

        # Reflection: для code/project ИЛИ если пользователь включил скилл
        should_reflect = (route in _REFLECTION_ROUTES) or use_reflection
        if should_reflect and answer.strip():
            ref = run_reflection_loop(model_name=model_name, profile_name=profile_name, user_input=user_input, draft_text=answer, review_text="Улучши.", context=ctx)
            answer = ref.get("answer") or answer

        result = {"ok": True, "answer": answer, "timeline": timeline, "tool_results": tool_results, "meta": {"model_name": model_name, "profile_name": profile_name, "route": route, "tools": selected, "run_id": run["run_id"]}}
        _HISTORY.finish_run(run["run_id"], result)
        return result
    except Exception as exc:
        err = {"ok": False, "answer": "", "timeline": timeline + [{"step": "error", "title": "Ошибка", "status": "error", "detail": str(exc)}], "tool_results": tool_results, "meta": {"error": str(exc), "run_id": run["run_id"]}}
        _HISTORY.finish_run(run["run_id"], err)
        return err


# ═══════════════════════════════════════════════════════════════
# run_agent_stream
# ═══════════════════════════════════════════════════════════════

def run_agent_stream(*, model_name, profile_name, user_input, use_memory=True, use_library=True, use_reflection=False, history=None):
    history = _trim_history(history or [])
    timeline, tool_results = [], []
    planner = PlannerV2Service()
    run = _HISTORY.start_run(user_input)
    try:
        plan = planner.plan(user_input)
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = plan.get("route", "chat")
        selected = [t for t in plan.get("tools", []) if not (t == "memory_search" and not use_memory) and not (t == "library_context" and not use_library)]

        # Умная память: извлекаем факты
        try:
            extract_and_save(user_input)
        except Exception:
            pass

        if "web_search" in selected:
            yield {"token": "", "done": False, "phase": "searching", "message": "Ищу в интернете и загружаю страницы..."}
        elif selected:
            yield {"token": "", "done": False, "phase": "tools", "message": "Подготовка..."}

        ctx = _collect_context(profile_name=profile_name, user_input=user_input, tools=selected, tool_results=tool_results, timeline=timeline, use_reflection=use_reflection)

        # Умная память + RAG
        try:
            mem_ctx = get_relevant_context(user_input, max_items=5)
            if _HAS_RAG:
                rag_ctx = get_rag_context(user_input, max_items=3)
                if rag_ctx:
                    mem_ctx = (mem_ctx + "\n\n" + rag_ctx) if mem_ctx else rag_ctx
            if mem_ctx:
                ctx = mem_ctx + "\n\n" + ctx if ctx else mem_ctx
        except Exception:
            pass

        yield {"token": "", "done": False, "phase": "thinking", "message": "Генерирую ответ..."}

        prompt = _build_prompt(user_input, ctx)
        full_text = ""
        for token in run_chat_stream(model_name=model_name, profile_name=profile_name, user_input=prompt, history=history):
            full_text += token
            yield {"token": token, "done": False}

        should_reflect = (route in _REFLECTION_ROUTES) or use_reflection
        if should_reflect and full_text.strip():
            yield {"token": "", "done": False, "phase": "reflecting", "message": "Проверяю..."}
            ref = run_reflection_loop(model_name=model_name, profile_name=profile_name, user_input=user_input, draft_text=full_text, review_text="Улучши.", context=ctx)
            refined = ref.get("answer", "")
            if refined and refined != full_text:
                full_text = refined
                yield {"token": "", "done": False, "phase": "reflection_replace", "full_text": refined}

        meta = {"model_name": model_name, "profile_name": profile_name, "route": route, "tools": selected, "run_id": run["run_id"]}
        _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": full_text, "meta": meta})
        yield {"token": "", "done": True, "full_text": full_text, "meta": meta, "timeline": timeline}
    except Exception as exc:
        _HISTORY.finish_run(run["run_id"], {"ok": False, "error": str(exc)})
        yield {"token": "", "done": True, "error": str(exc), "full_text": ""}
