from __future__ import annotations

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.tool_service import run_tool

router = APIRouter(prefix="/api/browser", tags=["browser"])


class BrowserSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    max_results: int = Field(default=5, ge=1, le=20)


class BrowserStep(BaseModel):
    action: str = Field(..., min_length=1)
    selector: str | None = None
    value: str | None = None
    name: str | None = None
    timeout_ms: int = Field(default=10000, ge=100, le=120000)
    limit: int | None = Field(default=20, ge=1, le=200)
    path: str | None = None
    full_page: bool | None = True


class BrowserRunRequest(BaseModel):
    start_url: str = Field(..., min_length=1)
    steps: list[BrowserStep] = Field(default_factory=list)
    headless: bool = True


@router.post("/search")
def browser_search(payload: BrowserSearchRequest):
    result = run_tool("browser_search", {"query": payload.query, "max_results": payload.max_results})
    return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")


@router.post("/run")
def browser_run(payload: BrowserRunRequest):
    result = run_tool(
        "browser_run",
        {
            "start_url": payload.start_url,
            "steps": [step.model_dump() for step in payload.steps],
            "headless": payload.headless,
        },
    )
    return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
