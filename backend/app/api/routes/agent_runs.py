from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any

from app.services.unified_agent_service import UnifiedAgentService

router = APIRouter(prefix="/api/agent-runs", tags=["agent-runs"])
agent_service = UnifiedAgentService()

class RunRequest(BaseModel):
    query: str
    web_sources: List[Dict[str, Any]] = []

class KnowledgeAddRequest(BaseModel):
    texts: List[str]
    source: str = "kb"

@router.post("/run")
def run_agent(request: RunRequest):
    return agent_service.run(request.query, request.web_sources)

@router.post("/knowledge/add")
def add_knowledge(request: KnowledgeAddRequest):
    added = agent_service.add_knowledge(request.texts, request.source)
    return {"added": added}

@router.get("/history")
def history():
    return {"runs": agent_service.history.list_runs()}
