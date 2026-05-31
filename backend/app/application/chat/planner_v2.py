"""
Planner v2 — keyword-based query classifier.

For each user query the planner returns a `route` (chat/code/code_agent/
multi_agent/project/research/image) and a list of `tools` to enable
(web_search / project_mode / project_patch / python_executor /
memory_search / library_context / image_gen).

Keyword matching:
  - Each keyword is matched against the query with Unicode word
    boundaries (re.UNICODE \b). Substring noise like 'barcode'
    triggering the 'code' keyword no longer happens.
  - Suffix `*` enables prefix-of-word matching: `код*` matches `код`,
    `коды`, `кодом`, `кодинг`, but NOT `штрихкод` (because the stem
    must start at a word boundary).
  - Weight defaults to `len(keyword.split())` so longer phrases like
    `напиши код` outweigh single-word noise; an explicit dict-shape
    keyword `(text, weight)` lets us override per-trigger.

Order of decisions (high to low priority):
  1. Empty query → chat
  2. Image keywords present and score >= 1 → image
  3. Multi-agent keywords with high score → multi_agent
  4. Code-agent keywords (read+edit+run patterns) → code_agent
  5. max(scores) over {research, project, code} → that route
  6. Tie-breaks: code+project → code; python in chat → code
  7. Chat → research escalation via _needs_web
"""
from __future__ import annotations

import re
from typing import Any

from app.application.chat.temporal_intent import detect_temporal_intent
from app.application.web_query_planner.runtime import plan_web_query


# Each bag is a tuple of (pattern, weight). Weight defaults to len(words)
# if omitted via the helper. Patterns ending in `*` are prefix-stems.

def _w(items: list) -> list[tuple[str, int]]:
    """Normalize a mixed list of strings and (str, weight) tuples."""
    out: list[tuple[str, int]] = []
    for it in items:
        if isinstance(it, tuple):
            text, weight = it
            out.append((str(text), int(weight)))
        else:
            text = str(it)
            words = max(1, len(text.rstrip("*").split()))
            out.append((text, words))
    return out


_RESEARCH_WORDS = _w([
    # English
    "search", ("documentation", 2), "docs", "site", "official", ("article", 2),
    "news", ("review", 2), ("compare", 2), ("comparison", 2), ("tutorial", 2),
    ("guide", 2), "wikipedia", "how to", "blog post",
    # Russian
    ("найди", 2), ("найти", 2), ("найдите", 2), ("поиск", 2), ("поищ*", 2),
    "загугли", ("источник", 2), "источник*", "ссыл*", "сайт",
    ("статья", 2), "стат*", ("новость", 2), ("новости", 2), "новост*",
    "что нового", "последние", "актуальн*", ("свеж*", 2),
    ("обзор", 2), "сравни*", "сравнен*", "руководств*", "гайд",
    "вики", "энциклопеди*", ("туториал", 2),
    "информация о", "расскажи про", "что такое", "who is", "what is",
    ("биография", 2), "биограф*", ("история", 2),
])

_NEEDS_WEB_PATTERNS = _w([
    "кто такой", "кто такая", "кто это", "who is", "who are",
    "что такое", "what is", "what are",
    "когда", "when", "where", "где находится",
    "сколько стоит", "price", "цена", "цены", "курс",
    "какой счет", "score", "результат", "results",
    "погода", "weather",
    "последние новости", "latest", "recent", "текущий", "current",
    "сейчас", "today", "сегодня",
    "как дела у", "что происходит",
    "топ", "лучш*", "best", "top",
    "рекомендуй", "recommend", "посоветуй",
])

_PROJECT_WORDS = _w([
    "project", "проект*", "repo", "репозитор*", "repository",
    "файл*", "backend", "frontend", "fullstack", "структур*", "tree",
    "read file", "project tree", "кодовая база", "codebase",
    "папк*", "folder", "directory", "каталог*",
    ("открой проект", 3), ("покажи файл", 3), ("show file", 2),
    ("покажи структуру", 3), ("show structure", 2),
    "модул*", "module", ("компонент*", 2), "component",
    "namespace", "пакет*", "package",
    "зависимост*", "dependencies",
])

_CODE_WORDS = _w([
    # English (strong)
    ("write code", 3), "implement*", ("refactor*", 2), ("optimiz*", 2),
    ("debug*", 2), ("unit test", 3), ("lint*", 2), ("typing", 2),
    "decod*", "encod*",
    # Russian (strong)
    ("напиши код", 3), ("напиши функцию", 3), ("напиши класс", 3),
    ("напиши тесты", 3), ("напиши тест", 3),
    ("реализуй", 2), ("реализаци*", 2),
    ("добавь функцию", 3), ("добавь метод", 3),
    ("исправь баг", 3), ("исправь ошибку", 3), ("исправь", 2),
    ("отладь", 2), ("оптимизируй", 2), ("отрефактори*", 2),
    ("патч", 2), "патчинг", ("рефактор*", 2),
    "тест", "тестир*",
    "декодир*", "кодир*",
    # Action verbs on code
    ("замени", 2), ("update file", 2), ("rewrite file", 2),
    ("создай файл", 2), ("create file", 2),
    ("удали функци*", 3), ("удали метод", 3),
    "допиш*", ("измени", 2),
    "fix", "patch", "bug", "error",
    ("create function", 2), ("add function", 2),
])


# Co-occurrence rules for code_agent: pairs of verb classes that
# together imply a multi-step file-touching task. Each rule fires
# independently — total score is sum.
_VERB_PATTERNS: dict[str, re.Pattern[str]] = {}


def _verb_regex(words: list[str]) -> re.Pattern[str]:
    """Compile a regex that matches any of `words` (each can have trailing *)."""
    parts = []
    for w in words:
        if w.endswith("*"):
            stem = w[:-1]
            parts.append(re.escape(stem) + r"\w*")
        else:
            parts.append(re.escape(w))
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE | re.UNICODE)


def _init_verb_patterns() -> None:
    global _VERB_PATTERNS
    _VERB_PATTERNS = {
        "read": _verb_regex(["прочитай", "читай", "read", "просмотри"]),
        "edit": _verb_regex(["поправ*", "исправ*", "fix", "update", "замени", "edit"]),
        "run":  _verb_regex(["запусти", "run", "выполни", "test*", "pytest", "проверь"]),
        "create": _verb_regex(["создай", "create", "make", "напиши файл", "сделай файл"]),
        "find": _verb_regex(["найди", "search", "find", "ищи", "scan", "просканируй"]),
        "apply": _verb_regex(["примени", "apply", "почини*"]),
    }


_init_verb_patterns()


_CODE_AGENT_RULES = [
    # (left_verbs, right_verbs, weight, label)
    ("read", "edit", 4, "read+edit"),       # "прочитай foo.py и поправь баг"
    ("create", "run", 4, "create+run"),     # "создай тест и запусти"
    ("find", "edit", 3, "find+edit"),       # "найди и замени"
    ("run", "edit", 3, "run+edit"),         # "запусти тесты и поправь"
    ("apply", "run", 3, "apply+run"),       # "примени патч и проверь"
]


def _score_code_agent(query: str) -> int:
    """Co-occurrence scorer: pairs of verb classes that imply multi-step."""
    present = {key: bool(pat.search(query)) for key, pat in _VERB_PATTERNS.items()}
    total = 0
    for left, right, weight, _label in _CODE_AGENT_RULES:
        if present.get(left) and present.get(right):
            total += weight
    return total

_MULTI_AGENT_WORDS = _w([
    ("распиши план", 3), ("составь план", 3), ("план рефактор*", 3),
    ("разбей задачу", 3), ("декомпозиц*", 2),
    ("комплексн*", 2), ("полный анализ", 3), ("глубокий анализ", 3),
    ("code review всего", 3), ("code review проект*", 3),
    ("проведи ревью", 3), ("сделай ревью", 3), ("ревью проекта", 3),
    ("research and implement", 3), ("исследуй и реализуй", 3),
    ("исследуй и напиши", 3),
    ("мульти-агент", 2), ("мультиагент", 2), ("multi-agent", 2),
    ("с рефлекс*", 2), ("с проверкой и", 2),
    ("аудит кода", 2), ("audit code", 2),
    ("полное тестирование", 3), ("прогон тестов", 2),
    ("end-to-end", 2), ("e2e", 2),
])

_PYTHON_WORDS = _w([
    # Only explicit Python-REPL or numerical-eval triggers. Bare verbs
    # like 'запусти' / 'run' were too greedy — they're better handled
    # by code_agent co-occurrence (run+edit, create+run).
    ("python script", 3), ("execute python", 2), ("run python", 2),
    ("выполни в python", 3), ("выполни python", 2),
    ("посчитай в python", 3), ("python repl", 2),
    ("eval expression", 2), ("evaluate expression", 2),
    ("interactive python", 2),
    ("pandas", 1), ("numpy", 1), ("matplotlib", 1),
])

_MEMORY_WORDS = _w([
    "remember", "memory", "помнишь", ("что ты помнишь", 3),
    "запомни", "вспомни", "из памяти", ("сохрани в память", 3),
])

_LIBRARY_WORDS = _w([
    "library", "библиотек*", "документ*",
    ("file from library", 3), ("загруженный файл", 2), ("uploaded file", 2),
    "из файла", "в файле", ("прочитай файл", 2), ("содержимое файла", 2),
])

_IMAGE_WORDS = _w([
    # English
    ("draw", 2), ("generate image", 2), ("make image", 2), ("create image", 2),
    "flux", "sdxl", ("stable diffusion", 2),
    # Russian
    ("нарисуй", 2), ("нарисов*", 2),
    ("сгенерируй картинк*", 3), ("сгенерируй изображ*", 3),
    ("сделай картинк*", 3), ("создай картинк*", 3),
    "картинк*", "изображени*", "иллюстраци*", "рисун*",
    ("в стиле", 2), ("style of", 2),
])

_CHAT_ONLY_PATTERNS = _w([
    "привет", "здравствуй", "hello", "hi ", "hey",
    "спасибо", "thanks", ("thank you", 2),
    "пока", "bye", "goodbye",
    ("как тебя зовут", 3), ("what is your name", 3),
    "ты кто", "кто ты",
    ("помоги мне написать", 3), ("помоги мне составить", 3),
    "напиши текст", "напиши письмо", "напиши email",
    ("переведи", 2), ("translate", 2),
    ("объясни разницу", 2), ("explain the difference", 3),
    ("придумай", 2), ("invent", 2), ("сочини", 2),
])


def _compile_pattern(text: str) -> re.Pattern[str]:
    """Build a Unicode-aware word-boundary regex. Trailing `*` means
    'prefix at word start' (e.g. `код*` matches `код`, `коды`, `кодом`,
    but not `штрихкод`).
    """
    text = text.strip()
    if text.endswith("*"):
        stem = text[:-1]
        # Word-start boundary then stem then any word chars
        return re.compile(r"(?<!\w)" + re.escape(stem) + r"\w*", re.IGNORECASE | re.UNICODE)
    # Strict whole-word match — handles phrases too (escape preserves spaces)
    return re.compile(r"(?<!\w)" + re.escape(text) + r"(?!\w)", re.IGNORECASE | re.UNICODE)


def _compile_bag(bag: list[tuple[str, int]]) -> list[tuple[re.Pattern[str], int]]:
    return [(_compile_pattern(text), weight) for text, weight in bag]


# Default (code-shipped) bag definitions — fallback when no DB override.
_DEFAULT_BAGS_SRC: dict[str, list[tuple[str, int]]] = {
    "research": _RESEARCH_WORDS,
    "project": _PROJECT_WORDS,
    "code": _CODE_WORDS,
    "multi_agent": _MULTI_AGENT_WORDS,
    "python": _PYTHON_WORDS,
    "memory": _MEMORY_WORDS,
    "library": _LIBRARY_WORDS,
    "image": _IMAGE_WORDS,
    "chat_only": _CHAT_ONLY_PATTERNS,
    "needs_web": _NEEDS_WEB_PATTERNS,
}

# Pre-compile from defaults at import time. Mutated by refresh_planner().
_BAGS: dict[str, list[tuple[re.Pattern[str], int]]] = {
    key: _compile_bag(src) for key, src in _DEFAULT_BAGS_SRC.items()
}


def _parse_user_keyword(item: str) -> tuple[str, int]:
    """User keywords are strings, optionally with ':<weight>' suffix.

    Examples:
      'найди'      → ('найди', 1)
      'найди:3'    → ('найди', 3)
      'код*'       → ('код*', 1)
      'напиши код:3' → ('напиши код', 3)
    """
    text = str(item).strip()
    if ":" in text:
        # split on the LAST colon so multi-word triggers don't break
        head, _, tail = text.rpartition(":")
        try:
            weight = int(tail.strip())
            return (head.strip(), max(1, weight))
        except ValueError:
            pass
    # auto-weight by word count
    words = max(1, len(text.rstrip("*").split()))
    return (text, words)


def refresh_planner(user_bags: dict[str, list[str]] | None = None) -> dict[str, int]:
    """Recompile pattern bags. If `user_bags` is given, those keys override
    the shipped defaults. Bags not present in user_bags fall back to defaults.

    Returns a summary {bag_key: keyword_count} for diagnostics.
    """
    user_bags = user_bags or {}
    summary: dict[str, int] = {}
    for key, default_src in _DEFAULT_BAGS_SRC.items():
        user_items = user_bags.get(key)
        if isinstance(user_items, list) and user_items:
            parsed = [_parse_user_keyword(it) for it in user_items]
            _BAGS[key] = _compile_bag(parsed)
            summary[key] = len(parsed)
        else:
            _BAGS[key] = _compile_bag(default_src)
            summary[key] = len(default_src)
    return summary


def _autoload_user_bags() -> None:
    """One-shot at module import: try to load user overrides from DB. If the
    DB layer isn't ready or empty, defaults stay in place.
    """
    try:
        from app.application.elira_memory.settings import get_planner_keywords
        user = get_planner_keywords()
        if user:
            refresh_planner(user)
    except Exception:
        # DB / settings layer not available yet — keep defaults
        pass


_autoload_user_bags()


def get_defaults_as_strings() -> dict[str, list[str]]:
    """Expose the shipped defaults as plain ['text:weight', ...] lists for
    the UI's 'reset to defaults' / seed flow.
    """
    out: dict[str, list[str]] = {}
    for key, items in _DEFAULT_BAGS_SRC.items():
        lines: list[str] = []
        for text, weight in items:
            auto_weight = max(1, len(text.rstrip("*").split()))
            if weight == auto_weight:
                lines.append(text)
            else:
                lines.append(f"{text}:{weight}")
        out[key] = lines
    return out


def _score(query: str, bag_key: str) -> int:
    """Sum of weights for every pattern in the bag that matches the query."""
    total = 0
    for pattern, weight in _BAGS[bag_key]:
        if pattern.search(query):
            total += weight
    return total


def _all_scores(query: str) -> dict[str, int]:
    scores = {key: _score(query, key) for key in _BAGS}
    # code_agent is computed via co-occurrence rules (not a bag)
    scores["code_agent"] = _score_code_agent(query)
    return scores


def _needs_web(query: str, temporal: dict[str, Any], scores: dict[str, int]) -> bool:
    if temporal.get("requires_web"):
        return True
    if temporal.get("stable_historical"):
        return False
    if scores["needs_web"] > 0:
        return True

    starters = (
        "кто ", "что ", "где ", "когда ", "сколько ",
        "какой ", "какая ", "какие ", "зачем ", "почему ",
        "how ", "what ", "who ", "where ", "when ", "which ",
    )
    if any(query.startswith(starter) for starter in starters):
        if scores["chat_only"] == 0 and scores["code"] == 0:
            return True
    return False


class PlannerV2Service:
    def plan(self, query: str) -> dict[str, Any]:
        normalized_query = (query or "").lower().strip()
        if not normalized_query:
            return {
                "route": "chat",
                "tools": [],
                "query": query,
                "strategy": "planner_v5_empty",
                "temporal": detect_temporal_intent(query),
                "web_plan": {"is_multi_intent": False, "subqueries": []},
                "scores": {},
            }

        temporal = detect_temporal_intent(query)
        scores = _all_scores(normalized_query)

        tools: list[str] = []
        route = "chat"

        # 1. Image route — checked first because image triggers are very specific
        if scores["image"] > 0:
            route = "image"
            tools.append("image_gen")

        # 2. Multi-agent — for complex orchestration tasks
        elif scores["multi_agent"] >= 2:
            route = "multi_agent"
            tools.append("multi_agent_workflow")

        # 3. Code-agent — for multi-step file operations
        elif scores["code_agent"] >= 3 or (scores["code_agent"] > 0 and scores["code"] > 0 and scores["project"] > 0):
            route = "code_agent"
            tools.append("code_agent_loop")

        else:
            # 4. Standard scoring across main routes
            main = {
                "research": scores["research"],
                "project": scores["project"],
                "code": scores["code"],
            }
            best = max(main, key=lambda k: main[k])
            if main[best] > 0:
                route = best
            if scores["code"] > 0 and scores["project"] > 0:
                route = "code"
            if scores["python"] > 0 and route == "chat":
                route = "code"

            # 5. Escalate chat → research if query needs web
            if route == "chat" and _needs_web(normalized_query, temporal, scores):
                route = "research"

        # Chat-only patterns should win against weak signals from other bags
        if scores["chat_only"] >= 2 and scores["code"] == 0 and scores["project"] == 0 and scores["image"] == 0:
            # Strong chat marker, no other strong signal — stay in chat
            if route in ("research",) and scores["research"] < 2:
                route = "chat"

        # Tools based on resolved route
        if route == "research":
            tools.append("web_search")
        if route in ("project", "code", "code_agent"):
            tools.append("project_mode")
        if route in ("code", "code_agent"):
            tools.append("project_patch")
        if scores["python"] > 0:
            tools.append("python_executor")
        if scores["memory"] > 0 or route in ("project", "code", "code_agent", "research", "multi_agent"):
            tools.append("memory_search")
        if scores["library"] > 0 or route in ("project", "code", "code_agent"):
            tools.append("library_context")

        # Dedupe while preserving order
        tools = list(dict.fromkeys(tools))

        web_plan = (
            plan_web_query(query, temporal)
            if "web_search" in tools
            else {"is_multi_intent": False, "subqueries": []}
        )

        return {
            "route": route,
            "tools": tools,
            "query": query,
            "strategy": "planner_v5",
            "scores": scores,
            "temporal": temporal,
            "web_plan": web_plan,
        }
