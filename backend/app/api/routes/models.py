from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.models_service import get_models

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def list_models():
    return JSONResponse(
        content=get_models(),
        media_type="application/json; charset=utf-8",
    )
