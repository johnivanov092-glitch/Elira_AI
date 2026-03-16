from __future__ import annotations

import json
from pathlib import Path
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

BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
MEMORY_STORE = DATA_DIR / "memory_store.json"


def _read_json(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def list_tools() -> dict[str, Any]:
    tools = [
        {"name": "search_memory"},
        {"name": "search_web"},
        {"name": "research_web"},
        {"name": "browser_search"},
        {"name": "multi_web_search"},
        {"name": "python_execute"},
        {"name": "list_project_tree"},
        {"name": "read_project_file"},
        {"name": "write_project_file"},
        {"name": "search_project"},
        {"name": "preview_project_patch"},
        {"name": "apply_project_patch"},
        {"name": "replace_in_file"},
        {"name": "list_library"},
        {"name": "build_library_context"},
    ]
    return {"ok": True, "tools": tools, "count": len(tools)}


def search_memory_tool(profile: str, query: str, limit: int = 5) -> dict[str, Any]:
    store = _read_json(MEMORY_STORE)
    items = store.get(profile, [])
    q = (query or "").lower().strip()

    scored = []
    for item in items:
        text = str(item.get("text", ""))
        low = text.lower()
        score = sum(1 for token in q.split() if token and token in low)
        if score > 0:
            scored.append((score, item))

    scored.sort(key=lambda x: (-x[0], x[1].get("created_at", "")))
    result_items = [item for _, item in scored[:limit]]

    return {
        "ok": True,
        "profile": profile,
        "query": query,
        "items": result_items,
        "count": len(result_items),
    }


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
        if isinstance(results, list):
            context = "\n".join(
                f"{r.get('title','')} - {r.get('snippet','')}"
                for r in results[:5]
                if isinstance(r, dict)
            )
            return {
                "ok": True,
                "query": query,
                "results": results,
                "count": len(results),
                "context": context,
            }
        return results

    if tool_name == "browser_search":
        from app.services.browser_agent import BrowserAgent
        agent = BrowserAgent()
        return agent.search(
            str(args.get("query", "")),
            max_results=int(args.get("max_results", 5)),
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

    if tool_name == "list_library":
        return list_library_files()

    if tool_name == "build_library_context":
        return build_library_context()

    return {"ok": False, "error": f"Unknown tool: {tool_name}"}
