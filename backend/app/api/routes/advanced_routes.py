"""
advanced_routes.py — роуты для Multi-agent, RAG, Project mode.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form
from pydantic import BaseModel

router = APIRouter(prefix="/api/advanced", tags=["advanced"])


# ═══════════════════════════════════════════════════════════════
# MULTI-AGENT
# ═══════════════════════════════════════════════════════════════

class MultiAgentRequest(BaseModel):
    query: str
    model_name: str = "qwen3:8b"
    context: str = ""
    agents: list[str] = ["researcher", "programmer", "analyst"]


@router.post("/multi-agent")
def run_multi(payload: MultiAgentRequest):
    from app.services.multi_agent_chain import run_multi_agent
    return run_multi_agent(
        query=payload.query,
        model_name=payload.model_name,
        context=payload.context,
        agents=payload.agents,
    )


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


# ═══════════════════════════════════════════════════════════════
# PROJECT MODE
# ═══════════════════════════════════════════════════════════════

# Храним текущий открытый проект
_project_path: str = ""

BLOCKED_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build", ".next", ".cache", "target", ".idea", ".vs"}
TEXT_EXTS = {".py",".js",".jsx",".ts",".tsx",".css",".html",".json",".yml",".yaml",".toml",".ini",".md",".txt",".rs",".go",".java",".c",".cpp",".h",".sh",".bat",".sql",".xml",".csv",".env",".bas",".vba",".cls"}


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
    global _project_path
    p = Path(payload.path).resolve()
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": f"Директория не найдена: {payload.path}"}
    _project_path = str(p)
    return {"ok": True, "path": _project_path, "name": p.name}


@router.get("/project/info")
def project_info():
    if not _project_path:
        return {"ok": False, "error": "Проект не открыт"}
    p = Path(_project_path)
    return {"ok": True, "path": _project_path, "name": p.name, "exists": p.exists()}


@router.get("/project/tree")
def project_tree(max_depth: int = 3, max_items: int = 300):
    """Дерево файлов проекта."""
    if not _project_path:
        return {"ok": False, "error": "Проект не открыт", "items": []}
    root = Path(_project_path)
    if not root.exists():
        return {"ok": False, "error": "Путь не существует", "items": []}

    items = []
    def walk(dir_path, depth, prefix=""):
        if depth > max_depth or len(items) >= max_items:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.name.startswith(".") and entry.name not in (".env",):
                continue
            if entry.name in BLOCKED_DIRS:
                continue
            rel = str(entry.relative_to(root)).replace("\\", "/")
            if entry.is_dir():
                items.append({"path": rel, "type": "dir", "name": entry.name})
                walk(entry, depth + 1)
            else:
                items.append({"path": rel, "type": "file", "name": entry.name, "size": entry.stat().st_size, "ext": entry.suffix.lower()})
            if len(items) >= max_items:
                return
    walk(root, 0)
    return {"ok": True, "items": items, "count": len(items), "root": _project_path}


@router.post("/project/read")
def read_project_file(payload: ReadFileRequest):
    """Читает файл из проекта."""
    if not _project_path:
        return {"ok": False, "error": "Проект не открыт"}
    full = (Path(_project_path) / payload.path).resolve()
    if not str(full).startswith(_project_path):
        return {"ok": False, "error": "Выход за пределы проекта"}
    if not full.exists() or not full.is_file():
        return {"ok": False, "error": f"Файл не найден: {payload.path}"}
    try:
        content = full.read_text(encoding="utf-8", errors="replace")[:payload.max_chars]
        return {"ok": True, "path": payload.path, "content": content, "size": full.stat().st_size}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/project/search")
def search_in_project(payload: SearchProjectRequest):
    """Поиск текста по файлам проекта."""
    if not _project_path:
        return {"ok": False, "error": "Проект не открыт"}
    root = Path(_project_path)
    query = payload.query.lower()
    results = []

    for fpath in root.rglob("*"):
        if len(results) >= payload.max_results:
            break
        if not fpath.is_file():
            continue
        if fpath.suffix.lower() not in TEXT_EXTS:
            continue
        rel = str(fpath.relative_to(root)).replace("\\", "/")
        if any(b in rel for b in BLOCKED_DIRS):
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(content.split("\n"), 1):
                if query in line.lower():
                    results.append({"path": rel, "line": i, "text": line.strip()[:200]})
                    if len(results) >= payload.max_results:
                        break
        except Exception:
            continue

    return {"ok": True, "items": results, "count": len(results), "query": payload.query}


@router.get("/project/close")
def close_project():
    global _project_path
    _project_path = ""
    return {"ok": True}
