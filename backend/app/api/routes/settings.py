from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas.settings import SettingsUpdateRequest
from app.services.settings_service import get_settings_payload, update_settings_payload

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def settings():
    return JSONResponse(
        content=get_settings_payload(),
        media_type="application/json; charset=utf-8",
    )


@router.post("")
def settings_update(payload: SettingsUpdateRequest):
    return JSONResponse(
        content=update_settings_payload(payload.model_name, payload.profile_name),
        media_type="application/json; charset=utf-8",
    )
