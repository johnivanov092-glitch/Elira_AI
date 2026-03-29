from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.services.persona_service import (
    get_persona_status,
    get_persona_version,
    list_persona_candidates,
    rollback_persona,
)


router = APIRouter(prefix="/api/persona", tags=["persona"])


@router.get("/status")
def persona_status():
    return JSONResponse(
        content=get_persona_status(),
        media_type="application/json; charset=utf-8",
    )


@router.get("/version")
def persona_version(version: int | None = None):
    return JSONResponse(
        content={"ok": True, "item": get_persona_version(version)},
        media_type="application/json; charset=utf-8",
    )


@router.get("/candidates")
def persona_candidates(limit: int = 20):
    items = list_persona_candidates(limit=limit)
    return JSONResponse(
        content={"ok": True, "items": items, "count": len(items)},
        media_type="application/json; charset=utf-8",
    )


@router.post("/rollback/{version}")
def persona_rollback(version: int):
    try:
        payload = rollback_persona(version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return JSONResponse(
        content=payload,
        media_type="application/json; charset=utf-8",
    )
