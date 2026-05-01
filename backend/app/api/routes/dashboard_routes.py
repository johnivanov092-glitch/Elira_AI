"""HTTP layer for the dashboard statistics endpoint.

All aggregation logic lives in ``app.application.dashboard.runtime``.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.application.dashboard import runtime as dash_runtime

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def dashboard_stats() -> dict:
    return dash_runtime.compute_dashboard_stats()
