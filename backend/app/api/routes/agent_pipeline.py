from fastapi import APIRouter
from pydantic import BaseModel
from app.services.agent_pipeline import AgentPipeline

router = APIRouter(prefix="/api/agent", tags=["agent"])
pipeline = AgentPipeline()

class Query(BaseModel):
    query: str

@router.post("/run")
def run_agent(q: Query):
    return pipeline.run(q.query)
