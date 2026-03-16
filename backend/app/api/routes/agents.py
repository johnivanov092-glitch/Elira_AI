from __future__ import annotations

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.agents_service import run_agent

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentRunRequest(BaseModel):
    model_name: str = Field(..., min_length=1)
    profile_name: str = Field(..., min_length=1)
    user_input: str = Field(..., min_length=1)
    use_memory: bool = True
    use_library: bool = True


@router.post("/run")
def agents_run(payload: AgentRunRequest):
    try:
        result = run_agent(
            model_name=payload.model_name,
            profile_name=payload.profile_name,
            user_input=payload.user_input,
            use_memory=payload.use_memory,
            use_library=payload.use_library,
            history=[],
        )
        return JSONResponse(
            content=jsonable_encoder(result),
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        fallback = {
            "ok": False,
            "answer": "",
            "timeline": [
                {
                    "step": "route_error",
                    "title": "Ошибка route /api/agents/run",
                    "status": "error",
                    "detail": str(exc),
                }
            ],
            "tool_results": [],
            "meta": {
                "error": str(exc),
                "route": "/api/agents/run",
            },
        }
        return JSONResponse(
            content=jsonable_encoder(fallback),
            media_type="application/json; charset=utf-8",
        )
