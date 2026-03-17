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

@app.get("/health")
def health():
    return {"status": "ok"}
