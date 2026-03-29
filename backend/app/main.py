from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.elira_state import router as elira_state_router
from app.api.routes.project_brain import router as project_brain_router
from app.api.routes.elira_execute import router as elira_execute_router
from app.api.routes.elira_patch import router as elira_patch_router
from app.api.routes.elira_devtools import router as elira_devtools_router
from app.api.routes.elira_task_runner import router as elira_task_runner_router
from app.api.routes.elira_supervisor import router as elira_supervisor_router
from app.api.routes.elira_phase19 import router as elira_phase19_router
from app.api.routes.elira_phase20 import router as elira_phase20_router
from app.api.routes.elira_phase20_queue import router as elira_phase20_queue_router
from app.api.routes.elira_phase20_state import router as elira_phase20_state_router
from app.api.routes.elira_phase21 import router as elira_phase21_router
from app.api.routes.elira_stabilization import router as elira_stabilization_router

from app.api.routes.chat import router as chat_router
from app.api.routes.models import router as models_router
from app.api.routes.memory import router as memory_router
from app.api.routes.library import router as library_router
from app.api.routes.profiles import router as profiles_router
from app.api.routes.agents import router as agents_router
from app.api.routes.files import router as files_router
from app.api.routes.pdf_routes import router as pdf_router
from app.api.routes.tools_exec import router as tools_exec_router
from app.api.routes.smart_memory_routes import router as smart_memory_router
from app.api.routes.file_ops import router as file_ops_router
from app.api.routes.terminal import router as terminal_router
from app.api.routes.library_sqlite import router as library_sqlite_router
from app.api.routes.advanced_routes import router as advanced_router
from app.api.routes.skills_routes import router as skills_router
from app.api.routes.skills_extra_routes import router as skills_extra_router
from app.api.routes.image_routes import router as image_router
from app.api.routes.git_routes import router as git_router
from app.api.routes.web_search_routes import router as web_search_router
from app.api.routes.dashboard_routes import router as dashboard_router
from app.api.routes.autopipeline_routes import router as autopipeline_router
from app.api.routes.task_planner_routes import router as task_planner_router
from app.api.routes.telegram_routes import router as telegram_router

app = FastAPI(title="Elira AI API")

# CORS: localhost + LAN (для mobile mode).
# Regex покрывает: 127.0.0.1, localhost, и любой LAN IP (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "tauri://localhost",
        "http://tauri.localhost",
    ],
    allow_origin_regex=r"https?://(127\.0\.0\.1|localhost|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(elira_state_router)
app.include_router(project_brain_router)
app.include_router(elira_execute_router)
app.include_router(elira_patch_router)
app.include_router(elira_devtools_router)
app.include_router(elira_task_runner_router)
app.include_router(elira_supervisor_router)
app.include_router(elira_phase19_router)
app.include_router(elira_phase20_router)
app.include_router(elira_phase20_queue_router)
app.include_router(elira_phase20_state_router)
app.include_router(elira_phase21_router)
app.include_router(elira_stabilization_router)

app.include_router(chat_router)
app.include_router(models_router)
app.include_router(memory_router)
app.include_router(library_router)
app.include_router(profiles_router)
app.include_router(agents_router)
app.include_router(files_router)
app.include_router(pdf_router)
app.include_router(tools_exec_router)
app.include_router(smart_memory_router)
app.include_router(file_ops_router)
app.include_router(terminal_router)
app.include_router(library_sqlite_router)
app.include_router(advanced_router)
app.include_router(skills_router)
app.include_router(skills_extra_router)
app.include_router(image_router)
app.include_router(git_router)
app.include_router(web_search_router)
app.include_router(dashboard_router)
app.include_router(autopipeline_router)
app.include_router(task_planner_router)
app.include_router(telegram_router)

@app.get("/health")
def health():
    return {"status": "ok", "service": "elira-ai-api"}
