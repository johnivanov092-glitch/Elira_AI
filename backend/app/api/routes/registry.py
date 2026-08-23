from __future__ import annotations

from app.api.routes.advanced_routes import router as advanced_router
from app.api.routes.chat_agent import router as chat_agent_router
from app.api.routes.code_agent_routes import router as code_agent_router
from app.api.routes.drift import router as drift_router
from app.api.routes.elira_state import router as elira_state_router
from app.api.routes.event_bus_routes import router as event_bus_router
from app.api.routes.library_sqlite import router as library_sqlite_router
from app.api.routes.media_routes import router as media_router
from app.api.routes.models import router as models_router
from app.api.routes.persona import router as persona_router
from app.api.routes.profiles import router as profiles_router
from app.api.routes.skills_routes import router as skills_router
from app.api.routes.voice_routes import router as voice_router
from app.api.routes.workflow_routes import router as workflow_router

ALL_ROUTERS = (
    elira_state_router,
    chat_agent_router,
    models_router,
    profiles_router,
    persona_router,
    library_sqlite_router,
    media_router,
    advanced_router,
    skills_router,
    event_bus_router,
    workflow_router,
    voice_router,
    code_agent_router,
    drift_router,
)

__all__ = ["ALL_ROUTERS"]
