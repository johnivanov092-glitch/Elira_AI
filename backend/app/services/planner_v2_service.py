from __future__ import annotations
from typing import Any


class PlannerV2Service:
    """
    Stage 5 planner:
    - chat
    - research
    - project
    - code
    """

    def plan(self, query: str) -> dict[str, Any]:
        q = (query or "").lower().strip()

        route = "chat"
        tools: list[str] = []

        research_words = [
            "search", "найди", "поиск", "documentation", "docs", "источник",
            "site", "сайт", "api docs", "official"
        ]

        project_words = [
            "project", "repo", "repository", "файл", "backend", "frontend",
            "структура", "tree", "read file", "project tree", "кодовая база"
        ]

        code_words = [
            "fix", "patch", "refactor", "bug", "error", "исправь", "патч",
            "рефактор", "исправление", "замени", "update file", "rewrite file"
        ]

        python_words = [
            "python", "script", "скрипт", "calc", "json", "csv", "execute python"
        ]

        memory_words = [
            "remember", "memory", "помнишь", "что ты помнишь"
        ]

        library_words = [
            "library", "библиотека", "документ", "file from library"
        ]

        if any(w in q for w in research_words):
            route = "research"
            tools.append("web_search")

        if any(w in q for w in project_words):
            if route == "chat":
                route = "project"
            tools.append("project_mode")

        if any(w in q for w in code_words):
            route = "code"
            if "project_mode" not in tools:
                tools.append("project_mode")
            tools.append("project_patch")

        if any(w in q for w in python_words):
            if route == "chat":
                route = "code"
            tools.append("python_executor")

        if any(w in q for w in memory_words):
            tools.append("memory_search")

        if any(w in q for w in library_words):
            tools.append("library_context")

        if route in {"project", "code", "research"} and "memory_search" not in tools:
            tools.append("memory_search")

        if route in {"project", "code"} and "library_context" not in tools:
            tools.append("library_context")

        tools = list(dict.fromkeys(tools))

        return {
            "route": route,
            "tools": tools,
            "query": query,
            "strategy": "planner_v2_stage5"
        }
