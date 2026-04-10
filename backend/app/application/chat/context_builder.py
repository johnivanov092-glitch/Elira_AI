from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


TimelineAppender = Callable[[list[dict[str, Any]], str, str, str, str], None]
ToolRunner = Callable[[str, dict[str, Any]], dict[str, Any]]
TemporalWebSearchRunner = Callable[
    [str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None],
    str,
]


def strip_frontend_project_context(user_input: str) -> str:
    """Remove the appended frontend project block from the user prompt."""
    text = user_input or ""
    marker = "\n\nОткрыт проект:"
    pos = text.find(marker)
    if pos >= 0:
        return text[:pos].rstrip()
    return text


def _build_project_context_from_tools(
    *,
    user_input: str,
    run_tool_func: ToolRunner,
    tool_results: list[dict[str, Any]],
) -> str:
    try:
        tree = run_tool_func("list_project_tree", {"max_depth": 3, "max_items": 200})
        search = run_tool_func("search_project", {"query": user_input, "max_hits": 20})
        tool_results.append({"tool": "project", "result": {"tree": tree.get("count", 0), "hits": search.get("count", 0)}})
        snippets = search.get("items") or search.get("results") or []
        if not snippets:
            return ""

        rendered = [
            "- " + (
                item.get("path", "") + ": " + (item.get("snippet", "") or item.get("preview", ""))
                if isinstance(item, dict)
                else str(item)
            )
            for item in snippets[:10]
        ]
        return "Из проекта:\n" + "\n".join(rendered)
    except Exception:
        return ""


def _build_project_context_from_open_project() -> str:
    # TODO: move this state lookup out of the API route module during route consolidation.
    try:
        from app.api.routes.advanced_routes import _project_path

        if not _project_path:
            return ""

        root = Path(_project_path)
        if not root.exists():
            return ""

        file_list: list[str] = []
        for file_path in sorted(root.rglob("*"))[:50]:
            if not file_path.is_file():
                continue
            if any(blocked in str(file_path) for blocked in [".git", "node_modules", "__pycache__", ".venv", "dist"]):
                continue
            file_list.append(str(file_path.relative_to(root)))

        if not file_list:
            return ""

        return f"Открыт проект: {root.name}\nФайлы ({len(file_list)}):\n" + "\n".join("- " + item for item in file_list[:30])
    except Exception:
        return ""


def collect_context(
    *,
    profile_name: str,
    user_input: str,
    tools: list[str],
    tool_results: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    run_tool_func: ToolRunner,
    append_timeline: TimelineAppender,
    temporal_web_search_func: TemporalWebSearchRunner,
    use_reflection: bool = False,
    temporal: dict[str, Any] | None = None,
    web_plan: dict[str, Any] | None = None,
) -> str:
    del use_reflection

    parts: list[str] = []
    for tool_name in tools:
        try:
            if tool_name == "memory_search":
                result = run_tool_func("search_memory", {"profile": profile_name, "query": user_input, "limit": 5})
                tool_results.append({"tool": "search_memory", "result": result})
                items = result.get("items", [])
                append_timeline(timeline, "tool_memory", "Память", "done", str(result.get("count", 0)))
                if items:
                    parts.append("Из памяти:\n" + "\n".join("- " + item.get("text", "") for item in items))
                continue

            if tool_name == "library_context":
                append_timeline(timeline, "tool_library", "Библиотека", "skip", "Фронтенд")
                continue

            if tool_name == "web_search":
                web_ctx = temporal_web_search_func(
                    user_input,
                    timeline,
                    tool_results,
                    temporal=temporal,
                    web_plan=web_plan,
                )
                if web_ctx:
                    parts.append(web_ctx)
                continue

            if tool_name == "project_mode":
                project_ctx = _build_project_context_from_tools(
                    user_input=user_input,
                    run_tool_func=run_tool_func,
                    tool_results=tool_results,
                )
                if not project_ctx:
                    project_ctx = _build_project_context_from_open_project()

                if project_ctx:
                    parts.append(project_ctx)
                    append_timeline(timeline, "tool_project", "Проект", "done", "Контекст загружен")
                else:
                    append_timeline(timeline, "tool_project", "Проект", "skip", "Не открыт")
                continue

            if tool_name == "python_executor":
                append_timeline(timeline, "tool_python", "Python", "ready", "Выполнение по запросу")
                continue

            if tool_name == "project_patch":
                append_timeline(timeline, "tool_patch", "Патчинг", "ready", "")
                continue
        except Exception as exc:
            append_timeline(timeline, "tool_" + tool_name, tool_name, "error", str(exc))

    return "\n\n".join(part for part in parts if part.strip())
