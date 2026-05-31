from __future__ import annotations

from fastapi import APIRouter

from app.application.runtime.status import get_runtime_status


router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("/status")
def runtime_status() -> dict:
    return get_runtime_status()
