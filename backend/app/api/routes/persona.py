from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.application.persona.service import (
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


@router.get("/triggers")
def persona_triggers():
    """Proactivity triggers and their status (Living Persona step C)."""
    from app.application.persona.proactive import list_triggers

    return JSONResponse(
        content=list_triggers(),
        media_type="application/json; charset=utf-8",
    )


@router.get("/suggestions")
def persona_suggestions(only_unread: bool = True):
    """Pending proactive suggestions (scheduled check-in queue)."""
    from app.application.persona.proactive import list_pending

    items = list_pending(only_unread=only_unread)
    return JSONResponse(
        content={"ok": True, "suggestions": items, "count": len(items)},
        media_type="application/json; charset=utf-8",
    )


@router.post("/suggestions/{suggestion_id}/read")
def persona_suggestion_read(suggestion_id: int):
    from app.application.persona.proactive import mark_read

    return mark_read(suggestion_id)


@router.post("/suggestions/{suggestion_id}/dismiss")
def persona_suggestion_dismiss(suggestion_id: int):
    from app.application.persona.proactive import dismiss_suggestion

    return dismiss_suggestion(suggestion_id)


@router.post("/suggestions/{suggestion_id}/respond")
def persona_suggestion_respond(suggestion_id: int, decision: str):
    """Resolve an enable_ask: decision = approve | deny."""
    from app.application.persona.proactive import respond_suggestion

    if decision not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'deny'")
    return respond_suggestion(suggestion_id, decision)


@router.get("/proactive-config")
def persona_proactive_config():
    from app.application.persona.proactive import get_proactive_config

    return JSONResponse(
        content={"ok": True, **get_proactive_config()},
        media_type="application/json; charset=utf-8",
    )


class CheckinTimeRequest(BaseModel):
    checkin_time: str


@router.put("/proactive-config")
def persona_set_proactive_config(payload: CheckinTimeRequest):
    from app.application.persona.proactive import set_checkin_time

    result = set_checkin_time(payload.checkin_time)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail="checkin_time must be HH:MM")
    return result


@router.get("/mood")
def persona_mood():
    """Elira's current mood (transient coloring; auto-drift + decay)."""
    from app.application.persona.mood import get_mood

    return JSONResponse(
        content={"ok": True, "mood": get_mood()},
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
