from __future__ import annotations

from app.api.routes.advanced_routes import router as advanced_router
from app.api.routes.agent_monitor_routes import router as agent_monitor_router
from app.api.routes.agent_registry_routes import router as agent_registry_router
from app.api.routes.agents import router as agents_router
from app.api.routes.autopipeline_routes import router as autopipeline_router
from app.api.routes.chat import router as chat_router
from app.api.routes.code_agent_routes import router as code_agent_router
from app.api.routes.dashboard_routes import router as dashboard_router
from app.api.routes.elira_patch import router as elira_patch_router
from app.api.routes.elira_state import router as elira_state_router
from app.api.routes.event_bus_routes import router as event_bus_router
from app.api.routes.file_ops import router as file_ops_router
from app.api.routes.files import router as files_router
from app.api.routes.git_routes import router as git_router
from app.api.routes.image_routes import router as image_router
from app.api.routes.library import router as library_router
from app.api.routes.library_sqlite import router as library_sqlite_router
from app.api.routes.memory import router as memory_router
from app.api.routes.models import router as models_router
from app.api.routes.pdf_routes import router as pdf_router
from app.api.routes.persona import router as persona_router
from app.api.routes.profiles import router as profiles_router
from app.api.routes.project_brain import router as project_brain_router
from app.api.routes.runtime import router as runtime_router
from app.api.routes.skills_extra_routes import router as skills_extra_router
from app.api.routes.skills_routes import router as skills_router
from app.api.routes.smart_memory_routes import router as smart_memory_router
from app.api.routes.spotlight_routes import router as spotlight_router
from app.api.routes.task_planner_routes import router as task_planner_router
from app.api.routes.telegram_routes import router as telegram_router
from app.api.routes.terminal import router as terminal_router
from app.api.routes.tool_registry_routes import router as tool_registry_router
from app.api.routes.tools_exec import router as tools_exec_router
from app.api.routes.web_search_routes import router as web_search_router
from app.api.routes.workflow_routes import router as workflow_router

ALL_ROUTERS = (
    elira_state_router,
    project_brain_router,
    elira_patch_router,
    chat_router,
    models_router,
    memory_router,
    library_router,
    profiles_router,
    agents_router,
    files_router,
    persona_router,
    runtime_router,
    pdf_router,
    tools_exec_router,
    smart_memory_router,
    spotlight_router,
    file_ops_router,
    terminal_router,
    library_sqlite_router,
    advanced_router,
    skills_router,
    skills_extra_router,
    image_router,
    git_router,
    web_search_router,
    dashboard_router,
    autopipeline_router,
    task_planner_router,
    telegram_router,
    agent_registry_router,
    event_bus_router,
    workflow_router,
    agent_monitor_router,
    tool_registry_router,
    code_agent_router,
)

__all__ = ["ALL_ROUTERS"]
