from fastapi import APIRouter

from app.application.dashboard.dashboard_service import get_dashboard_stats

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def dashboard_stats() -> dict:
    return get_dashboard_stats()
