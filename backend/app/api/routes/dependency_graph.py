from __future__ import annotations

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.tool_service import run_tool

router = APIRouter(prefix="/api/dependency-graph", tags=["dependency-graph"])


class FileDependenciesRequest(BaseModel):
    path: str = Field(..., min_length=1)


class ReverseDependenciesRequest(BaseModel):
    target: str = Field(..., min_length=1)


class HotspotsRequest(BaseModel):
    top_n: int = Field(default=10, ge=1, le=100)


@router.get("/build")
def build_dependency_graph():
    try:
        result = run_tool("code_dependency_graph", {})
        return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
    except Exception as exc:
        return JSONResponse(
            content=jsonable_encoder({"ok": False, "route": "/api/dependency-graph/build", "error": str(exc)}),
            media_type="application/json; charset=utf-8",
        )


@router.post("/file")
def file_dependencies(payload: FileDependenciesRequest):
    try:
        result = run_tool("file_dependencies", {"path": payload.path})
        return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
    except Exception as exc:
        return JSONResponse(
            content=jsonable_encoder({
                "ok": False,
                "route": "/api/dependency-graph/file",
                "path": payload.path,
                "error": str(exc),
            }),
            media_type="application/json; charset=utf-8",
        )


@router.post("/reverse")
def reverse_dependencies(payload: ReverseDependenciesRequest):
    try:
        result = run_tool("reverse_dependencies", {"target": payload.target})
        return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
    except Exception as exc:
        return JSONResponse(
            content=jsonable_encoder({
                "ok": False,
                "route": "/api/dependency-graph/reverse",
                "target": payload.target,
                "error": str(exc),
            }),
            media_type="application/json; charset=utf-8",
        )


@router.post("/hotspots")
def dependency_hotspots(payload: HotspotsRequest):
    try:
        result = run_tool("dependency_hotspots", {"top_n": payload.top_n})
        return JSONResponse(content=jsonable_encoder(result), media_type="application/json; charset=utf-8")
    except Exception as exc:
        return JSONResponse(
            content=jsonable_encoder({
                "ok": False,
                "route": "/api/dependency-graph/hotspots",
                "top_n": payload.top_n,
                "error": str(exc),
            }),
            media_type="application/json; charset=utf-8",
        )
