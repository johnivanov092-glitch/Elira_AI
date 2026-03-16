from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.profiles_service import get_profiles

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("")
def list_profiles():
    return JSONResponse(
        content=get_profiles(),
        media_type="application/json; charset=utf-8",
    )
