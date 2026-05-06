from __future__ import annotations

from typing import Any


def build_builtin_tools() -> list[dict[str, Any]]:
    from app.application.git.runtime import git_commit as _git_commit_fn
    from app.application.git.runtime import git_status as _git_status_fn
    from app.application.library.runtime import build_library_context, list_library_files
    from app.application.project_brain.loop_service import ProjectBrainLoopService
    from app.application.project_brain.map_service import ProjectMapService
    from app.application.project_patch.service import ProjectPatchService
    from app.application.smart_memory import search_memory as smart_search_memory
    from app.domain.runtime.python_runner import execute_python
    from app.infrastructure.browser.agent import BrowserAgent
    from app.infrastructure.search.multisearch import WebMultiSearchService
    from app.infrastructure.search.web_search import research_web, search_web
    from app.infrastructure.storage.project_files import (
        list_project_tree,
        read_project_file,
        search_project,
        write_project_file,
    )

    def search_memory_tool(profile: str, query: str, limit: int = 5) -> dict[str, Any]:
        result = smart_search_memory(query=query, limit=max(1, int(limit)))
        result["profile"] = str(profile or "default")
        return result

    patch_service = ProjectPatchService()
    map_service = ProjectMapService()
    brain_service = ProjectBrainLoopService()

    return [
        {
            "name": "search_memory",
            "handler": lambda a: search_memory_tool(str(a.get("profile", "default")), str(a.get("query", "")), int(a.get("limit", 5))),
            "display_name": "Search Memory",
            "display_name_ru": "Поиск в памяти",
            "category": "memory",
            "description": "Search semantic memory for relevant facts",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "profile": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
        },
        {
            "name": "search_web",
            "handler": lambda a: {"ok": True, "query": str(a.get("query", "")), "results": search_web(str(a.get("query", "")), max_results=int(a.get("max_results", 5)))},
            "display_name": "Search Web",
            "display_name_ru": "Поиск в интернете",
            "category": "web",
            "description": "Search the web for current information",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
        },
        {
            "name": "research_web",
            "handler": lambda a: {"ok": True, "query": str(a.get("query", "")), "results": (r := research_web(query=str(a.get("query", "")), max_results=int(a.get("max_results", 5)))), "count": len(r) if isinstance(r, list) else 0},
            "display_name": "Deep Research",
            "display_name_ru": "Глубокое исследование",
            "category": "web",
            "description": "Fetch and parse web pages for deep research",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
        },
        {
            "name": "browser_search",
            "handler": lambda a: BrowserAgent().search(str(a.get("query", "")), max_results=int(a.get("max_results", 5))),
            "display_name": "Browser Search",
            "display_name_ru": "Поиск через браузер",
            "category": "web",
            "description": "Search using headless browser",
        },
        {
            "name": "browser_run",
            "handler": lambda a: BrowserAgent().run(start_url=str(a.get("start_url", "")), steps=a.get("steps", []) if isinstance(a.get("steps", []), list) else [], headless=bool(a.get("headless", True))),
            "display_name": "Browser Run",
            "display_name_ru": "Запуск браузера",
            "category": "web",
            "description": "Run browser automation steps",
        },
        {
            "name": "multi_web_search",
            "handler": lambda a: WebMultiSearchService().search(str(a.get("query", "")), max_results=int(a.get("max_results", 5))),
            "display_name": "Multi Web Search",
            "display_name_ru": "Мульти-поиск",
            "category": "web",
            "description": "Search across multiple web engines",
        },
        {
            "name": "python_execute",
            "handler": lambda a: execute_python(str(a.get("code", ""))),
            "display_name": "Python Execute",
            "display_name_ru": "Выполнить Python",
            "category": "code",
            "description": "Execute Python code in a sandboxed subprocess",
            "parameters_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
        },
        {
            "name": "list_project_tree",
            "handler": lambda a: list_project_tree(int(a.get("max_depth", 3)), int(a.get("max_items", 400))),
            "display_name": "Project Tree",
            "display_name_ru": "Дерево проекта",
            "category": "project",
            "description": "List project file tree",
        },
        {
            "name": "read_project_file",
            "handler": lambda a: read_project_file(str(a.get("path", "")), int(a.get("max_chars", 12000))),
            "display_name": "Read File",
            "display_name_ru": "Прочитать файл",
            "category": "project",
            "description": "Read a project file",
            "parameters_schema": {"type": "object", "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}}, "required": ["path"]},
        },
        {
            "name": "write_project_file",
            "handler": lambda a: write_project_file(str(a.get("path", "")), str(a.get("content", ""))),
            "display_name": "Write File",
            "display_name_ru": "Записать файл",
            "category": "project",
            "description": "Write content to a project file",
            "parameters_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        },
        {
            "name": "search_project",
            "handler": lambda a: search_project(str(a.get("query", "")), int(a.get("max_hits", 50))),
            "display_name": "Search Project",
            "display_name_ru": "Поиск в проекте",
            "category": "project",
            "description": "Search project files by content",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
        {
            "name": "preview_project_patch",
            "handler": lambda a: patch_service.preview_patch(str(a.get("path", "")), str(a.get("new_content", "")), int(a.get("max_chars", 20000))),
            "display_name": "Preview Patch",
            "display_name_ru": "Предпросмотр патча",
            "category": "project",
            "description": "Preview file patch before applying",
        },
        {
            "name": "apply_project_patch",
            "handler": lambda a: patch_service.apply_patch(str(a.get("path", "")), str(a.get("new_content", ""))),
            "display_name": "Apply Patch",
            "display_name_ru": "Применить патч",
            "category": "project",
            "description": "Apply a file patch",
        },
        {
            "name": "replace_in_file",
            "handler": lambda a: patch_service.replace_in_file(str(a.get("path", "")), str(a.get("old_text", "")), str(a.get("new_text", "")), int(a.get("max_chars", 20000))),
            "display_name": "Replace in File",
            "display_name_ru": "Замена в файле",
            "category": "project",
            "description": "Replace text in a file",
        },
        {
            "name": "apply_replace_in_file",
            "handler": lambda a: patch_service.apply_replace_in_file(str(a.get("path", "")), str(a.get("old_text", "")), str(a.get("new_text", "")), int(a.get("max_chars", 20000))),
            "display_name": "Apply Replace",
            "display_name_ru": "Применить замену",
            "category": "project",
            "description": "Apply text replacement in a file",
        },
        {
            "name": "rollback_project_patch",
            "handler": lambda a: patch_service.rollback_patch(str(a.get("path", "")), str(a.get("backup_id", ""))),
            "display_name": "Rollback Patch",
            "display_name_ru": "Откатить патч",
            "category": "project",
            "description": "Rollback a file patch from backup",
        },
        {
            "name": "list_patch_backups",
            "handler": lambda a: patch_service.list_backups(path=str(a.get("path", "")).strip() or None, limit=int(a.get("limit", 20))),
            "display_name": "List Backups",
            "display_name_ru": "Список бэкапов",
            "category": "project",
            "description": "List available patch backups",
        },
        {
            "name": "git_status",
            "handler": lambda a: _git_status_fn(),
            "display_name": "Git Status",
            "display_name_ru": "Статус Git",
            "category": "system",
            "description": "Show git repository status",
        },
        {
            "name": "git_commit_push",
            "handler": lambda a: _git_commit_fn(message=str(a.get("message", "AI update")), add_all=True),
            "display_name": "Git Commit",
            "display_name_ru": "Git коммит",
            "category": "system",
            "description": "Commit and push changes",
        },
        {
            "name": "list_library",
            "handler": lambda a: list_library_files(),
            "display_name": "List Library",
            "display_name_ru": "Библиотека",
            "category": "memory",
            "description": "List indexed library files",
        },
        {
            "name": "build_library_context",
            "handler": lambda a: build_library_context(),
            "display_name": "Library Context",
            "display_name_ru": "Контекст библиотеки",
            "category": "memory",
            "description": "Build context from indexed library",
        },
        {
            "name": "project_map_scan",
            "handler": lambda a: map_service.build_map(max_depth=int(a.get("max_depth", 4)), max_items=int(a.get("max_items", 500))),
            "display_name": "Project Map",
            "display_name_ru": "Карта проекта",
            "category": "project",
            "description": "Build project structure map",
        },
        {
            "name": "project_map_search",
            "handler": lambda a: map_service.search(str(a.get("query", "")), max_hits=int(a.get("max_hits", 30))),
            "display_name": "Map Search",
            "display_name_ru": "Поиск по карте",
            "category": "project",
            "description": "Search project map",
        },
        {
            "name": "project_brain_analyze",
            "handler": lambda a: brain_service.analyze(focus=str(a.get("focus", "backend")), max_iterations=int(a.get("max_iterations", 3))),
            "display_name": "Brain Analyze",
            "display_name_ru": "Анализ проекта",
            "category": "project",
            "description": "Deep analysis of project structure",
        },
        {
            "name": "project_brain_loop",
            "handler": lambda a: brain_service.run_loop(path=str(a.get("path", "")), new_content=str(a.get("new_content", "")), message=str(a.get("message", "AI Project Brain patch")), max_iterations=int(a.get("max_iterations", 1)), auto_push=bool(a.get("auto_push", False))),
            "display_name": "Brain Loop",
            "display_name_ru": "Петля разработки",
            "category": "project",
            "description": "Iterative project development loop",
        },
    ]
