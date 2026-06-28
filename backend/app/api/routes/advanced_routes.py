"""
advanced_routes.py — роуты для Multi-agent, RAG, Project mode.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import json
import logging
import queue
import threading

from app.application.advanced import runtime as project_runtime

router = APIRouter(prefix="/api/advanced", tags=["advanced"])
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# MULTI-AGENT
# ═══════════════════════════════════════════════════════════════

class MultiAgentRequest(BaseModel):
    query: str
    model_name: str = "local-model"
    context: str = ""
    agents: list[str] = ["researcher", "programmer", "analyst"]
    use_reflection: bool = False
    use_orchestrator: bool = False
    project_root: str | None = None
    num_ctx: int | None = None


@router.post("/multi-agent")
def run_multi(payload: MultiAgentRequest):
    from app.application.workflows.multi_agent import run_multi_agent_workflow

    try:
        result = run_multi_agent_workflow(
            query=payload.query,
            model_name=payload.model_name,
            context=payload.context,
            agents=payload.agents,
            use_reflection=payload.use_reflection,
            use_orchestrator=payload.use_orchestrator,
            project_root=payload.project_root,
            num_ctx=payload.num_ctx,
        )
        if not isinstance(result, dict):
            return JSONResponse(status_code=502, content={"ok": False, "error": "Multi-agent вернул некорректный результат."})
        result.setdefault("ok", True)
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        logger.exception("/api/advanced/multi-agent failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": f"Multi-agent error: {e}"})


@router.post("/multi-agent/stream")
def run_multi_stream(payload: MultiAgentRequest):
    """Streaming sibling of /multi-agent.

    The workflow runs synchronously inside a worker thread; a progress_callback
    pushes one `{"type":"step", ...}` event per step into a queue that the SSE
    generator drains in real time, then a final `{"type":"done", ...}` event
    carries the same payload the sync route returns (report/timeline/results/…).
    The sync /multi-agent route stays intact for non-streaming callers.
    """
    from app.application.workflows.multi_agent import run_multi_agent_workflow

    events: "queue.Queue[dict]" = queue.Queue()
    _SENTINEL: dict = {"__end__": True}
    # Set when the client disconnects (Stop button tears down the SSE stream).
    # The workflow runs in a daemon thread that can't be interrupted mid-LLM
    # call, so cancellation is cooperative: run_multi_agent_workflow checks this
    # flag between steps and records the run as cancelled.
    cancel_event = threading.Event()

    def _on_progress(index: int, total: int, label: str) -> None:
        events.put({"type": "step", "index": index, "total": total, "label": label})

    def _worker() -> None:
        try:
            result = run_multi_agent_workflow(
                query=payload.query,
                model_name=payload.model_name,
                context=payload.context,
                agents=payload.agents,
                use_reflection=payload.use_reflection,
                use_orchestrator=payload.use_orchestrator,
                project_root=payload.project_root,
                num_ctx=payload.num_ctx,
                progress_callback=_on_progress,
                cancel_check=cancel_event.is_set,
            )
            if not isinstance(result, dict):
                events.put({"type": "error", "error": "Multi-agent вернул некорректный результат."})
            else:
                result.setdefault("ok", True)
                events.put({"type": "done", **result})
        except Exception as e:  # noqa: BLE001 - surfaced to the client as an SSE error event
            logger.exception("/api/advanced/multi-agent/stream failed")
            events.put({"type": "error", "error": f"Multi-agent error: {e}"})
        finally:
            events.put(_SENTINEL)

    def _generate():
        worker = threading.Thread(target=_worker, daemon=True)
        worker.start()
        try:
            while True:
                event = events.get()
                if event is _SENTINEL:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except GeneratorExit:
            # Client disconnected (Stop). Signal the worker to stop between
            # steps; it owns the same SQLite lifecycle and will mark the run
            # cancelled. Re-raise so the StreamingResponse closes cleanly.
            cancel_event.set()
            raise

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ═══════════════════════════════════════════════════════════════
# RAG ПАМЯТЬ
# ═══════════════════════════════════════════════════════════════

class RagAddRequest(BaseModel):
    text: str
    category: str = "fact"
    importance: int = 5

class RagSearchRequest(BaseModel):
    query: str
    limit: int = 5


@router.post("/rag/add")
def rag_add(payload: RagAddRequest):
    from app.application.rag_memory.service import add_to_rag

    return add_to_rag(payload.text, payload.category, payload.importance)


@router.post("/rag/search")
def rag_search(payload: RagSearchRequest):
    from app.application.rag_memory.service import search_rag

    return search_rag(payload.query, payload.limit)


@router.get("/rag/list")
def rag_list(limit: int = 50):
    from app.application.rag_memory.service import list_rag

    return list_rag(limit)


@router.delete("/rag/{item_id}")
def rag_delete(item_id: int):
    from app.application.rag_memory.service import delete_rag

    return delete_rag(item_id)


@router.get("/rag/stats")
def rag_get_stats():
    from app.application.rag_memory.service import rag_stats

    return rag_stats()


@router.delete("/rag/clear")
def rag_clear_category(category: str | None = None):
    """Bulk delete. If category is provided, deletes only items in that
    category; otherwise nukes everything in rag_items. Returns the
    number of rows removed.
    """
    from app.application.rag_memory.service import _conn  # type: ignore[attr-defined]

    conn = _conn()
    try:
        if category:
            cur = conn.execute("DELETE FROM rag_items WHERE category = ?", (category,))
        else:
            cur = conn.execute("DELETE FROM rag_items")
        conn.commit()
        return {"ok": True, "deleted": cur.rowcount, "category": category}
    finally:
        conn.close()


@router.post("/rag/prune")
def rag_prune(dry_run: bool = True, max_age_days: int = 30, max_importance: int = 3):
    """Decay/forgetting: evict stale, never-recalled machine-made memories
    (default category `agent_turn`). User facts/preferences/instructions are
    never touched. `dry_run=true` (the default) previews candidates without
    deleting — pass `dry_run=false` to actually prune.
    """
    from app.application.rag_memory.service import prune_rag

    return prune_rag(
        max_age_days=max_age_days,
        max_importance=max_importance,
        dry_run=dry_run,
    )


# ═══════════════════════════════════════════════════════════════
# PROJECT MODE
# ═══════════════════════════════════════════════════════════════

# Project-mode compatibility constants
BLOCKED_DIRS = project_runtime.BLOCKED_DIRS
TEXT_EXTS = project_runtime.TEXT_EXTS


class OpenProjectRequest(BaseModel):
    path: str


class ReadFileRequest(BaseModel):
    path: str
    max_chars: int = 20000
    # Optional explicit project root. When set, read is scoped to it instead of
    # the global open project — lets the code-agent drawer stay independent of
    # the chat's open project.
    root: str = ""


class SearchProjectRequest(BaseModel):
    query: str
    max_results: int = 20
    root: str = ""


class AddProjectRequest(BaseModel):
    path: str
    name: str = ""


class OpenNamedProjectRequest(BaseModel):
    # Either a saved project id, or a name/path to resolve in the registry.
    id: str = ""
    name: str = ""


@router.get("/projects")
def list_projects():
    """Saved project registry (name + path)."""
    from app.application.advanced import projects_registry
    return projects_registry.list_projects()


@router.post("/projects")
def add_project(payload: AddProjectRequest):
    """Add a project to the registry (validates the path exists)."""
    from app.application.advanced import projects_registry
    return projects_registry.add_project(payload.path, payload.name)


@router.delete("/projects/{project_id}")
def remove_project(project_id: str):
    from app.application.advanced import projects_registry
    return projects_registry.remove_project(project_id)


@router.post("/projects/open")
def open_named_project(payload: OpenNamedProjectRequest):
    """Resolve a saved project (by id or name/path) and make it the active one."""
    from app.application.advanced import projects_registry
    proj = None
    if payload.id:
        for item in projects_registry.list_projects().get("projects", []):
            if item["id"] == payload.id:
                proj = item
                break
    if proj is None:
        proj = projects_registry.resolve_project(payload.name)
    if proj is None:
        raise HTTPException(status_code=404, detail="Проект не найден в списке")
    opened = project_runtime.open_project(proj["path"])
    if opened.get("ok"):
        opened["id"] = proj["id"]
    return opened


@router.post("/project/open")
def open_project(payload: OpenProjectRequest):
    """Открывает проект по пути."""
    return project_runtime.open_project(payload.path)


@router.get("/project/info")
def project_info():
    return project_runtime.get_project_info()


@router.get("/project/tree")
def project_tree(max_depth: int = 3, max_items: int = 300, root: str = ""):
    """Дерево файлов проекта. `root` (опц.) — конкретный путь вместо глобального."""
    return project_runtime.project_tree(max_depth=max_depth, max_items=max_items, project_root=root or None)


@router.post("/project/read")
def read_project_file(payload: ReadFileRequest):
    """Читает файл из проекта."""
    return project_runtime.read_project_file(payload.path, max_chars=payload.max_chars, project_root=payload.root or None)


@router.post("/project/search")
def search_in_project(payload: SearchProjectRequest):
    """Поиск текста по файлам проекта."""
    return project_runtime.search_in_project(payload.query, max_results=payload.max_results, project_root=payload.root or None)


@router.get("/project/close")
def close_project():
    return project_runtime.close_project()
