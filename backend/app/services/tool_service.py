from __future__ import annotations

from typing import Any

from app.services.web_service import research_web, search_web
from app.services.python_runner import execute_python
from app.services.project_service import (
    list_project_tree,
    read_project_file,
    write_project_file,
    search_project,
)
from app.services.project_patch_service import ProjectPatchService
from app.services.library_service import (
    list_library_files,
    build_library_context,
)
from app.services.smart_memory import search_memory as smart_search_memory
from app.services.git_service import git_status as _git_status_fn, git_commit as _git_commit_fn
from app.services.project_map_service import ProjectMapService
from app.services.project_brain_loop_service import ProjectBrainLoopService



def list_tools() -> dict[str, Any]:
    tools = [
        {"name": "search_memory"},
        {"name": "search_web"},
        {"name": "research_web"},
        {"name": "browser_search"},
        {"name": "browser_run"},
        {"name": "multi_web_search"},
        {"name": "python_execute"},
        {"name": "list_project_tree"},
        {"name": "read_project_file"},
        {"name": "write_project_file"},
        {"name": "search_project"},
        {"name": "preview_project_patch"},
        {"name": "apply_project_patch"},
        {"name": "replace_in_file"},
        {"name": "apply_replace_in_file"},
        {"name": "rollback_project_patch"},
        {"name": "list_patch_backups"},
        {"name": "git_status"},
        {"name": "git_commit_push"},
        {"name": "list_library"},
        {"name": "build_library_context"},
        {"name": "project_map_scan"},
        {"name": "project_map_search"},
        {"name": "project_brain_analyze"},
        {"name": "project_brain_loop"},
    ]
    return {"ok": True, "tools": tools, "count": len(tools)}


def search_memory_tool(profile: str, query: str, limit: int = 5) -> dict[str, Any]:
    result = smart_search_memory(query=query, limit=max(1, int(limit)))
    result["profile"] = str(profile or "default")
    return result



def run_tool(tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}

    if tool_name == "search_memory":
        return search_memory_tool(
            str(args.get("profile", "default")),
            str(args.get("query", "")),
            int(args.get("limit", 5)),
        )

    if tool_name == "search_web":
        query = str(args.get("query", "")).strip()
        return {
            "ok": True,
            "query": query,
            "results": search_web(query, max_results=int(args.get("max_results", 5))),
        }

    if tool_name == "research_web":
        query = str(args.get("query", "")).strip()
        results = research_web(query=query, max_results=int(args.get("max_results", 5)))
        return {
            "ok": True,
            "query": query,
            "results": results,
            "count": len(results) if isinstance(results, list) else 0,
        }

    if tool_name == "browser_search":
        from app.services.browser_agent import BrowserAgent
        agent = BrowserAgent()
        return agent.search(
            str(args.get("query", "")),
            max_results=int(args.get("max_results", 5)),
        )

    if tool_name == "browser_run":
        from app.services.browser_agent import BrowserAgent
        agent = BrowserAgent()
        return agent.run(
            start_url=str(args.get("start_url", "")),
            steps=args.get("steps", []) if isinstance(args.get("steps", []), list) else [],
            headless=bool(args.get("headless", True)),
        )

    if tool_name == "multi_web_search":
        from app.services.web_multisearch_service import WebMultiSearchService
        service = WebMultiSearchService()
        return service.search(
            str(args.get("query", "")),
            max_results=int(args.get("max_results", 5)),
        )

    if tool_name == "python_execute":
        return execute_python(str(args.get("code", "")))

    if tool_name == "list_project_tree":
        return list_project_tree(
            int(args.get("max_depth", 3)),
            int(args.get("max_items", 400)),
        )

    if tool_name == "read_project_file":
        return read_project_file(
            str(args.get("path", "")),
            int(args.get("max_chars", 12000)),
        )

    if tool_name == "write_project_file":
        return write_project_file(
            str(args.get("path", "")),
            str(args.get("content", "")),
        )

    if tool_name == "search_project":
        return search_project(
            str(args.get("query", "")),
            int(args.get("max_hits", 50)),
        )

    if tool_name == "preview_project_patch":
        patch = ProjectPatchService()
        return patch.preview_patch(
            str(args.get("path", "")),
            str(args.get("new_content", "")),
            int(args.get("max_chars", 20000)),
        )

    if tool_name == "apply_project_patch":
        patch = ProjectPatchService()
        return patch.apply_patch(
            str(args.get("path", "")),
            str(args.get("new_content", "")),
        )

    if tool_name == "replace_in_file":
        patch = ProjectPatchService()
        return patch.replace_in_file(
            str(args.get("path", "")),
            str(args.get("old_text", "")),
            str(args.get("new_text", "")),
            int(args.get("max_chars", 20000)),
        )

    if tool_name == "apply_replace_in_file":
        patch = ProjectPatchService()
        return patch.apply_replace_in_file(
            str(args.get("path", "")),
            str(args.get("old_text", "")),
            str(args.get("new_text", "")),
            int(args.get("max_chars", 20000)),
        )

    if tool_name == "rollback_project_patch":
        patch = ProjectPatchService()
        return patch.rollback_patch(
            str(args.get("path", "")),
            str(args.get("backup_id", "")),
        )

    if tool_name == "list_patch_backups":
        patch = ProjectPatchService()
        return patch.list_backups(
            path=str(args.get("path", "")).strip() or None,
            limit=int(args.get("limit", 20)),
        )

    if tool_name == "git_status":
        return _git_status_fn()

    if tool_name == "git_commit_push":
        return _git_commit_fn(message=str(args.get("message", "AI update")), add_all=True)

    if tool_name == "list_library":
        return list_library_files()

    if tool_name == "build_library_context":
        return build_library_context()

    if tool_name == "project_map_scan":
        service = ProjectMapService()
        return service.build_map(
            max_depth=int(args.get("max_depth", 4)),
            max_items=int(args.get("max_items", 500)),
        )

    if tool_name == "project_map_search":
        service = ProjectMapService()
        return service.search(
            str(args.get("query", "")),
            max_hits=int(args.get("max_hits", 30)),
        )

    if tool_name == "project_brain_analyze":
        service = ProjectBrainLoopService()
        return service.analyze(
            focus=str(args.get("focus", "backend")),
            max_iterations=int(args.get("max_iterations", 3)),
        )

    if tool_name == "project_brain_loop":
        service = ProjectBrainLoopService()
        return service.run_loop(
            path=str(args.get("path", "")),
            new_content=str(args.get("new_content", "")),
            message=str(args.get("message", "AI Project Brain patch")),
            max_iterations=int(args.get("max_iterations", 1)),
            auto_push=bool(args.get("auto_push", False)),
        )

    return {"ok": False, "error": f"Unknown tool: {tool_name}"}
