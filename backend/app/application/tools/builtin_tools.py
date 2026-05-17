"""Builtin tool definition data.

Extracted from tool_registry.py — pure data module.
``get_builtin_tool_definitions`` is called once from ``seed_builtin_tools``
and returns the list of tool dicts (without the ``handler`` key) together with
the handlers as a separate list so the caller can ``register_tool`` each one.

Imports are kept lazy inside the factory to preserve the same circular-import
avoidance that tool_registry.py already relies on: tool_service imports
tool_registry at the top level, so tool_registry (and any module it imports at
top level) must NOT import tool_service at the top level.
"""
from __future__ import annotations

from typing import Any


def get_builtin_tool_definitions() -> list[dict[str, Any]]:
    """Return built-in tool dicts ready for ``register_tool(**d)``."""
    # Lazy imports — intentional: avoid circular dependency between
    # tool_registry <-> tool_service.
    from app.application.tools.tool_service import search_memory_tool
    from app.application.web.web_service import research_web, search_web
    from app.infrastructure.runtime.python_runner import execute_python
    from app.infrastructure.files.project_service import (
        list_project_tree,
        read_project_file,
        search_project,
        write_project_file,
    )
    from app.application.projects.project_patch_service import ProjectPatchService
    from app.application.library.library_service import (
        build_library_context,
        list_library_files,
    )
    from app.infrastructure.vcs.git_service import (
        git_commit as _git_commit_fn,
        git_status as _git_status_fn,
    )
    from app.application.projects.project_map_service import ProjectMapService
    from app.application.projects.project_brain_loop_service import ProjectBrainLoopService

    _patch = ProjectPatchService()
    _map_svc = ProjectMapService()
    _brain_svc = ProjectBrainLoopService()

    return [
        {
            "name": "search_memory",
            "handler": lambda a: search_memory_tool(str(a.get("profile", "default")), str(a.get("query", "")), int(a.get("limit", 5))),
            "display_name": "Search Memory", "display_name_ru": "Поиск в памяти",
            "category": "memory",
            "description": "Search semantic memory for relevant facts",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "profile": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
        },
        {
            "name": "search_web",
            "handler": lambda a: {"ok": True, "query": str(a.get("query", "")), "results": search_web(str(a.get("query", "")), max_results=int(a.get("max_results", 5)))},
            "display_name": "Search Web", "display_name_ru": "Поиск в интернете",
            "category": "web",
            "description": "Search the web for current information",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
        },
        {
            "name": "research_web",
            "handler": lambda a: {"ok": True, "query": str(a.get("query", "")), "results": (r := research_web(query=str(a.get("query", "")), max_results=int(a.get("max_results", 5)))), "count": len(r) if isinstance(r, list) else 0},
            "display_name": "Deep Research", "display_name_ru": "Глубокое исследование",
            "category": "web",
            "description": "Fetch and parse web pages for deep research",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
        },
        {
            "name": "browser_search",
            "handler": lambda a: __import__("app.application.agents.browser_agent", fromlist=["BrowserAgent"]).BrowserAgent().search(str(a.get("query", "")), max_results=int(a.get("max_results", 5))),
            "display_name": "Browser Search", "display_name_ru": "Поиск через браузер",
            "category": "web",
            "description": "Search using headless browser",
        },
        {
            "name": "browser_run",
            "handler": lambda a: __import__("app.application.agents.browser_agent", fromlist=["BrowserAgent"]).BrowserAgent().run(start_url=str(a.get("start_url", "")), steps=a.get("steps", []) if isinstance(a.get("steps", []), list) else [], headless=bool(a.get("headless", True))),
            "display_name": "Browser Run", "display_name_ru": "Запуск браузера",
            "category": "web",
            "description": "Run browser automation steps",
        },
        {
            "name": "multi_web_search",
            "handler": lambda a: __import__("app.infrastructure.search.web_multisearch_service", fromlist=["WebMultiSearchService"]).WebMultiSearchService().search(str(a.get("query", "")), max_results=int(a.get("max_results", 5))),
            "display_name": "Multi Web Search", "display_name_ru": "Мульти-поиск",
            "category": "web",
            "description": "Search across multiple web engines",
        },
        {
            "name": "python_execute",
            "handler": lambda a: execute_python(str(a.get("code", ""))),
            "display_name": "Python Execute", "display_name_ru": "Выполнить Python",
            "category": "code",
            "description": "Execute Python code in a sandboxed subprocess",
            "parameters_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
        },
        {
            "name": "list_project_tree",
            "handler": lambda a: list_project_tree(int(a.get("max_depth", 3)), int(a.get("max_items", 400))),
            "display_name": "Project Tree", "display_name_ru": "Дерево проекта",
            "category": "project",
            "description": "List project file tree",
        },
        {
            "name": "read_project_file",
            "handler": lambda a: read_project_file(str(a.get("path", "")), int(a.get("max_chars", 12000))),
            "display_name": "Read File", "display_name_ru": "Прочитать файл",
            "category": "project",
            "description": "Read a project file",
            "parameters_schema": {"type": "object", "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}}, "required": ["path"]},
        },
        {
            "name": "write_project_file",
            "handler": lambda a: write_project_file(str(a.get("path", "")), str(a.get("content", ""))),
            "display_name": "Write File", "display_name_ru": "Записать файл",
            "category": "project",
            "description": "Write content to a project file",
            "parameters_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        },
        {
            "name": "search_project",
            "handler": lambda a: search_project(str(a.get("query", "")), int(a.get("max_hits", 50))),
            "display_name": "Search Project", "display_name_ru": "Поиск в проекте",
            "category": "project",
            "description": "Search project files by content",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
        {
            "name": "preview_project_patch",
            "handler": lambda a: _patch.preview_patch(str(a.get("path", "")), str(a.get("new_content", "")), int(a.get("max_chars", 20000))),
            "display_name": "Preview Patch", "display_name_ru": "Предпросмотр патча",
            "category": "project",
            "description": "Preview file patch before applying",
        },
        {
            "name": "apply_project_patch",
            "handler": lambda a: _patch.apply_patch(str(a.get("path", "")), str(a.get("new_content", ""))),
            "display_name": "Apply Patch", "display_name_ru": "Применить патч",
            "category": "project",
            "description": "Apply a file patch",
        },
        {
            "name": "replace_in_file",
            "handler": lambda a: _patch.replace_in_file(str(a.get("path", "")), str(a.get("old_text", "")), str(a.get("new_text", "")), int(a.get("max_chars", 20000))),
            "display_name": "Replace in File", "display_name_ru": "Замена в файле",
            "category": "project",
            "description": "Replace text in a file",
        },
        {
            "name": "apply_replace_in_file",
            "handler": lambda a: _patch.apply_replace_in_file(str(a.get("path", "")), str(a.get("old_text", "")), str(a.get("new_text", "")), int(a.get("max_chars", 20000))),
            "display_name": "Apply Replace", "display_name_ru": "Применить замену",
            "category": "project",
            "description": "Apply text replacement in a file",
        },
        {
            "name": "rollback_project_patch",
            "handler": lambda a: _patch.rollback_patch(str(a.get("path", "")), str(a.get("backup_id", ""))),
            "display_name": "Rollback Patch", "display_name_ru": "Откатить патч",
            "category": "project",
            "description": "Rollback a file patch from backup",
        },
        {
            "name": "list_patch_backups",
            "handler": lambda a: _patch.list_backups(path=str(a.get("path", "")).strip() or None, limit=int(a.get("limit", 20))),
            "display_name": "List Backups", "display_name_ru": "Список бэкапов",
            "category": "project",
            "description": "List available patch backups",
        },
        {
            "name": "git_status",
            "handler": lambda a: _git_status_fn(),
            "display_name": "Git Status", "display_name_ru": "Статус Git",
            "category": "system",
            "description": "Show git repository status",
        },
        {
            "name": "git_commit_push",
            "handler": lambda a: _git_commit_fn(message=str(a.get("message", "AI update")), add_all=True),
            "display_name": "Git Commit", "display_name_ru": "Git коммит",
            "category": "system",
            "description": "Commit and push changes",
        },
        {
            "name": "list_library",
            "handler": lambda a: list_library_files(),
            "display_name": "List Library", "display_name_ru": "Библиотека",
            "category": "memory",
            "description": "List indexed library files",
        },
        {
            "name": "build_library_context",
            "handler": lambda a: build_library_context(),
            "display_name": "Library Context", "display_name_ru": "Контекст библиотеки",
            "category": "memory",
            "description": "Build context from indexed library",
        },
        {
            "name": "project_map_scan",
            "handler": lambda a: _map_svc.build_map(max_depth=int(a.get("max_depth", 4)), max_items=int(a.get("max_items", 500))),
            "display_name": "Project Map", "display_name_ru": "Карта проекта",
            "category": "project",
            "description": "Build project structure map",
        },
        {
            "name": "project_map_search",
            "handler": lambda a: _map_svc.search(str(a.get("query", "")), max_hits=int(a.get("max_hits", 30))),
            "display_name": "Map Search", "display_name_ru": "Поиск по карте",
            "category": "project",
            "description": "Search project map",
        },
        {
            "name": "project_brain_analyze",
            "handler": lambda a: _brain_svc.analyze(focus=str(a.get("focus", "backend")), max_iterations=int(a.get("max_iterations", 3))),
            "display_name": "Brain Analyze", "display_name_ru": "Анализ проекта",
            "category": "project",
            "description": "Deep analysis of project structure",
        },
        {
            "name": "project_brain_loop",
            "handler": lambda a: _brain_svc.run_loop(path=str(a.get("path", "")), new_content=str(a.get("new_content", "")), message=str(a.get("message", "AI Project Brain patch")), max_iterations=int(a.get("max_iterations", 1)), auto_push=bool(a.get("auto_push", False))),
            "display_name": "Brain Loop", "display_name_ru": "Петля разработки",
            "category": "project",
            "description": "Iterative project development loop",
        },
    ]
