"""HTTP layer for Multi-agent, RAG, and Project mode routes.

Multi-agent and RAG routes delegate via lazy imports to services.
Project mode operations are fully delegated to
``app.application.advanced.runtime``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.application.advanced import runtime as adv_runtime

router = APIRouter(prefix="/api/advanced", tags=["advanced"])
logger = logging.getLogger(__name__)


# ── Multi-agent ──────────────────────────────────────────────────────────────

class MultiAgentRequest(BaseModel):
    query: str
    model_name: str = "qwen3:8b"
    context: str = ""
    agents: list[str] = ["researcher", "programmer", "analyst"]
    use_reflection: bool = False
    use_orchestrator: bool = False


@router.post("/multi-agent")
def run_multi(payload: MultiAgentRequest):
    from app.services.multi_agent_chain import run_multi_agent
    try:
        result = run_multi_agent(
            query=payload.query,
            model_name=payload.model_name,
            context=payload.context,
            agents=payload.agents,
            use_reflection=payload.use_reflection,
            use_orchestrator=payload.use_orchestrator,
        )
        if not isinstance(result, dict):
            return JSONResponse(
                status_code=200,
                content={"ok": False, "error": "Multi-agent returned unexpected result type."},
            )
        result.setdefault("ok", True)
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        logger.exception("/api/advanced/multi-agent failed")
        return JSONResponse(status_code=200, content={"ok": False, "error": f"Multi-agent error: {e}"})


# ── RAG ──────────────────────────────────────────────────────────────────────

class RagAddRequest(BaseModel):
    text: str
    category: str = "fact"
    importance: int = 5


class RagSearchRequest(BaseModel):
    query: str
    limit: int = 5


@router.post("/rag/add")
def rag_add(payload: RagAddRequest):
    from app.services.rag_memory_service import add_to_rag
    return add_to_rag(payload.text, payload.category, payload.importance)


@router.post("/rag/search")
def rag_search(payload: RagSearchRequest):
    from app.services.rag_memory_service import search_rag
    return search_rag(payload.query, payload.limit)


@router.get("/rag/list")
def rag_list(limit: int = 50):
    from app.services.rag_memory_service import list_rag
    return list_rag(limit)


@router.delete("/rag/{item_id}")
def rag_delete(item_id: int):
    from app.services.rag_memory_service import delete_rag
    return delete_rag(item_id)


@router.get("/rag/stats")
def rag_get_stats():
    from app.services.rag_memory_service import rag_stats
    return rag_stats()


# ── Project mode ─────────────────────────────────────────────────────────────

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
    return adv_runtime.open_project(payload.path)


@router.get("/project/info")
def project_info():
    return adv_runtime.get_project_info()


@router.get("/project/tree")
def project_tree(max_depth: int = 3, max_items: int = 300):
    return adv_runtime.project_tree(max_depth, max_items)


@router.post("/project/read")
def read_project_file(payload: ReadFileRequest):
    return adv_runtime.read_project_file(payload.path, payload.max_chars)


@router.post("/project/search")
def search_in_project(payload: SearchProjectRequest):
    return adv_runtime.search_in_project(payload.query, payload.max_results)


@router.get("/project/close")
def close_project():
    return adv_runtime.close_project()
