from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.jarvis_state import router as jarvis_state_router
from app.api.routes.project_brain import router as project_brain_router
from app.api.routes.jarvis_execute import router as jarvis_execute_router
from app.api.routes.jarvis_patch import router as jarvis_patch_router
from app.api.routes.jarvis_devtools import router as jarvis_devtools_router
from app.api.routes.jarvis_task_runner import router as jarvis_task_runner_router
from app.api.routes.jarvis_supervisor import router as jarvis_supervisor_router
from app.api.routes.jarvis_phase19 import router as jarvis_phase19_router
from app.api.routes.jarvis_phase20 import router as jarvis_phase20_router
from app.api.routes.jarvis_phase20_queue import router as jarvis_phase20_queue_router
from app.api.routes.jarvis_phase20_state import router as jarvis_phase20_state_router
from app.api.routes.jarvis_phase21 import router as jarvis_phase21_router
from app.api.routes.jarvis_stabilization import router as jarvis_stabilization_router

from app.api.routes.chat import router as chat_router
from app.api.routes.models import router as models_router
from app.api.routes.memory import router as memory_router
from app.api.routes.library import router as library_router
from app.api.routes.profiles import router as profiles_router
from app.api.routes.agents import router as agents_router
from app.api.routes.files import router as files_router
from app.api.routes.tools_exec import router as tools_exec_router
from app.api.routes.smart_memory_routes import router as smart_memory_router
from app.api.routes.file_ops import router as file_ops_router
from app.api.routes.advanced_routes import router as advanced_router

app = FastAPI(title="Jarvis Work API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jarvis_state_router)
app.include_router(project_brain_router)
app.include_router(jarvis_execute_router)
app.include_router(jarvis_patch_router)
app.include_router(jarvis_devtools_router)
app.include_router(jarvis_task_runner_router)
app.include_router(jarvis_supervisor_router)
app.include_router(jarvis_phase19_router)
app.include_router(jarvis_phase20_router)
app.include_router(jarvis_phase20_queue_router)
app.include_router(jarvis_phase20_state_router)
app.include_router(jarvis_phase21_router)
app.include_router(jarvis_stabilization_router)

app.include_router(chat_router)
app.include_router(models_router)
app.include_router(memory_router)
app.include_router(library_router)
app.include_router(profiles_router)
app.include_router(agents_router)
app.include_router(files_router)
app.include_router(tools_exec_router)
app.include_router(smart_memory_router)
app.include_router(file_ops_router)
app.include_router(advanced_router)

@app.get("/health")
def health():
    return {"status": "ok", "service": "jarvis-work-api"}
