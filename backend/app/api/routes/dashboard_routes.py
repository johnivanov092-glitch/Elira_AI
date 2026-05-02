from __future__ import annotations

from fastapi import APIRouter

from app.application.dashboard import runtime as dashboard_runtime


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def dashboard_stats() -> dict:
    return dashboard_runtime.dashboard_stats()
