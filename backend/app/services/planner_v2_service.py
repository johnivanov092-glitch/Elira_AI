from __future__ import annotations

from typing import Any

from app.services.temporal_intent import detect_temporal_intent


_RESEARCH_WORDS = {
    "search",
    "найди",
    "поиск",
    "поищи",
    "загугли",
    "google",
    "documentation",
    "docs",
    "источник",
    "ссылка",
    "ссылки",
    "site",
    "сайт",
    "api docs",
    "official",
    "статья",
    "article",
    "новости",
    "news",
    "что нового",
    "последние",
    "актуальн",
    "обзор",
    "review",
    "сравни",
    "compare",
    "comparison",
    "информация о",
    "расскажи про",
    "что такое",
    "who is",
    "what is",
    "tutorial",
    "руководство",
    "гайд",
    "guide",
    "how to",
    "wikipedia",
    "вики",
}

_NEEDS_WEB_PATTERNS = {
    "кто такой",
    "кто такая",
    "кто это",
    "who is",
    "who are",
    "что такое",
    "what is",
    "what are",
    "когда",
    "when",
    "where",
    "где находится",
    "сколько стоит",
    "price",
    "цена",
    "курс",
    "какой счет",
    "score",
    "результат",
    "results",
    "погода",
    "weather",
    "последние новости",
    "latest",
    "recent",
    "текущий",
    "current",
    "сейчас",
    "today",
    "сегодня",
    "как дела у",
    "что происходит",
    "топ ",
    "лучший ",
    "лучшие ",
    "best ",
    "top ",
    "рекомендуй",
    "recommend",
    "посоветуй",
}

_PROJECT_WORDS = {
    "project",
    "repo",
    "repository",
    "файл",
    "файлы",
    "backend",
    "frontend",
    "структура",
    "tree",
    "read file",
    "project tree",
    "кодовая база",
    "папка",
    "folder",
    "directory",
    "каталог",
    "открой",
    "покажи файл",
    "show file",
    "модуль",
    "module",
    "компонент",
    "component",
}

_CODE_WORDS = {
    "fix",
    "patch",
    "refactor",
    "bug",
    "error",
    "исправь",
    "патч",
    "рефактор",
    "исправление",
    "замени",
    "update file",
    "rewrite file",
    "напиши код",
    "write code",
    "implement",
    "реализуй",
    "добавь функцию",
    "add function",
    "создай файл",
    "create file",
    "удали",
    "измени",
    "допиши",
    "оптимизируй",
    "optimize",
    "debug",
    "отладь",
    "тест",
    "test",
    "unit test",
    "lint",
    "типизация",
    "typing",
}

_PYTHON_WORDS = {
    "python",
    "script",
    "скрипт",
    "calc",
    "json",
    "csv",
    "execute python",
    "запусти",
    "выполни",
    "run",
    "вычисли",
    "посчитай",
    "calculate",
}

_MEMORY_WORDS = {
    "remember",
    "memory",
    "помнишь",
    "что ты помнишь",
    "запомни",
    "вспомни",
    "из памяти",
    "сохрани в память",
}

_LIBRARY_WORDS = {
    "library",
    "библиотека",
    "документ",
    "file from library",
    "загруженный файл",
    "uploaded file",
    "из файла",
    "в файле",
    "прочитай файл",
    "содержимое файла",
}

_CHAT_ONLY_PATTERNS = {
    "привет",
    "здравствуй",
    "hello",
    "hi ",
    "hey",
    "спасибо",
    "thanks",
    "thank you",
    "пока",
    "bye",
    "goodbye",
    "как тебя зовут",
    "what is your name",
    "ты кто",
    "кто ты",
    "помоги мне написать",
    "помоги мне составить",
    "напиши текст",
    "напиши письмо",
    "напиши email",
    "переведи",
    "translate",
    "объясни разницу",
    "explain the difference",
    "придумай",
    "invent",
    "сочини",
}


def _count(query: str, words: set[str]) -> int:
    return sum(1 for word in words if word in query)


def _needs_web(query: str, temporal: dict[str, Any]) -> bool:
    if temporal.get("requires_web"):
        return True
    if temporal.get("stable_historical"):
        return False
    if _count(query, _NEEDS_WEB_PATTERNS) > 0:
        return True

    starters = (
        "кто ",
        "что ",
        "где ",
        "когда ",
        "сколько ",
        "какой ",
        "какая ",
        "какие ",
        "зачем ",
        "почему ",
        "how ",
        "what ",
        "who ",
        "where ",
        "when ",
        "which ",
    )
    if any(query.startswith(starter) for starter in starters):
        if _count(query, _CHAT_ONLY_PATTERNS) == 0 and _count(query, _CODE_WORDS) == 0:
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
                "strategy": "planner_v4_empty",
                "temporal": detect_temporal_intent(query),
            }

        temporal = detect_temporal_intent(query)
        scores = {
            "research": _count(normalized_query, _RESEARCH_WORDS),
            "project": _count(normalized_query, _PROJECT_WORDS),
            "code": _count(normalized_query, _CODE_WORDS),
            "python": _count(normalized_query, _PYTHON_WORDS),
            "memory": _count(normalized_query, _MEMORY_WORDS),
            "library": _count(normalized_query, _LIBRARY_WORDS),
        }

        tools: list[str] = []
        route = "chat"

        main = {
            "research": scores["research"],
            "project": scores["project"],
            "code": scores["code"],
        }
        best = max(main, key=main.get)
        if main[best] > 0:
            route = best
        if scores["code"] > 0 and scores["project"] > 0:
            route = "code"
        if scores["python"] > 0 and route == "chat":
            route = "code"

        if route == "research":
            tools.append("web_search")
        if route in ("project", "code"):
            tools.append("project_mode")
        if route == "code":
            tools.append("project_patch")
        if scores["python"] > 0:
            tools.append("python_executor")

        if route == "chat" and "web_search" not in tools and _needs_web(normalized_query, temporal):
            route = "research"
            tools.append("web_search")

        if scores["memory"] > 0 or route in ("project", "code", "research"):
            tools.append("memory_search")
        if scores["library"] > 0 or route in ("project", "code"):
            tools.append("library_context")

        tools = list(dict.fromkeys(tools))

        return {
            "route": route,
            "tools": tools,
            "query": query,
            "strategy": "planner_v4",
            "scores": scores,
            "temporal": temporal,
        }
