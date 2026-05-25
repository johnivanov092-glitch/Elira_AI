"""
advanced_routes.py — роуты для Multi-agent, RAG, Project mode.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import logging

from app.application.advanced import runtime as project_runtime

router = APIRouter(prefix="/api/advanced", tags=["advanced"])
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# MULTI-AGENT
# ═══════════════════════════════════════════════════════════════

class MultiAgentRequest(BaseModel):
    query: str
    model_name: str = "qwen3:8b"
    context: str = ""
    agents: list[str] = ["researcher", "programmer", "analyst"]
    use_reflection: bool = False
    use_orchestrator: bool = False


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
        )
        if not isinstance(result, dict):
            return JSONResponse(status_code=200, content={"ok": False, "error": "Multi-agent вернул некорректный результат."})
        result.setdefault("ok", True)
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        logger.exception("/api/advanced/multi-agent failed")
        return JSONResponse(status_code=200, content={"ok": False, "error": f"Multi-agent error: {e}"})


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


class SearchProjectRequest(BaseModel):
    query: str
    max_results: int = 20


@router.post("/project/open")
def open_project(payload: OpenProjectRequest):
    """Открывает проект по пути."""
    return project_runtime.open_project(payload.path)


@router.get("/project/info")
def project_info():
    return project_runtime.get_project_info()


@router.get("/project/tree")
def project_tree(max_depth: int = 3, max_items: int = 300):
    """Дерево файлов проекта."""
    return project_runtime.project_tree(max_depth=max_depth, max_items=max_items)


@router.post("/project/read")
def read_project_file(payload: ReadFileRequest):
    """Читает файл из проекта."""
    return project_runtime.read_project_file(payload.path, max_chars=payload.max_chars)


@router.post("/project/search")
def search_in_project(payload: SearchProjectRequest):
    """Поиск текста по файлам проекта."""
    return project_runtime.search_in_project(payload.query, max_results=payload.max_results)


@router.get("/project/close")
def close_project():
    return project_runtime.close_project()
