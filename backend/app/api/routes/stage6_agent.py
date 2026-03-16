from fastapi import APIRouter
from pydantic import BaseModel
from app.services.stage6_agent_service import Stage6AgentService

router = APIRouter(prefix="/api/stage6", tags=["stage6"])
agent = Stage6AgentService()

class QueryRequest(BaseModel):
    query: str

@router.post("/run")
def run_agent(request: QueryRequest):
    return agent.run(request.query)
