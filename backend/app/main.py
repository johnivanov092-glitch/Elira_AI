from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.agents import router as agents_router
from app.api.routes.chat import router as chat_router
from app.api.routes.library import router as library_router
from app.api.routes.memory import router as memory_router
from app.api.routes.models import router as models_router
from app.api.routes.profiles import router as profiles_router
from app.api.routes.settings import router as settings_router
from app.api.routes.tools import router as tools_router
from app.api.routes.project_patch import router as project_patch_router
from app.api.routes.agent_supervisor import router as agent_supervisor_router
from app.api.routes.run_history import router as run_history_router

# Optional runtime routes (safe import)
try:
    from app.api.routes.browser_runtime import router as browser_runtime_router
except Exception:
    browser_runtime_router = None

try:
    from app.api.routes.desktop_runtime import router as desktop_runtime_router
except Exception:
    desktop_runtime_router = None


app = FastAPI(title="Jarvis Backend")


# -----------------------------
# CORS
# -----------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# ROOT
# -----------------------------

@app.get("/")
def root():
    return JSONResponse(
        content={
            "name": "Jarvis Backend",
            "status": "running",
            "docs": "http://127.0.0.1:8000/docs",
            "routes": [
                "/api/health",
                "/api/chat/send",
                "/api/models",
                "/api/profiles",
                "/api/settings",
                "/api/library/files",
                "/api/memory/profiles",
                "/api/agents/run",
                "/api/tools",
                "/api/tools/run",
                "/api/project/patch/preview",
                "/api/project/patch/apply",
                "/api/project/patch/replace",
                "/api/browser/search",
                "/api/browser/run",
                "/api/browser/screenshot",
                "/api/desktop/status",
                "/api/desktop/info",
                "/api/supervisor/status",
                "/api/supervisor/agents",
                "/api/supervisor/agents/register",
                "/api/supervisor/run",
                "/api/supervisor/runs",
                "/api/supervisor/events",
                "/api/supervisor/schedule",
                "/api/supervisor/jobs",
                "/api/supervisor/bootstrap",
                "/api/run-history/status",
                "/api/run-history/run",
                "/api/run-history/runs",
                "/api/run-history/runs/{run_id}",
            ],
        },
        media_type="application/json; charset=utf-8",
    )


# -----------------------------
# HEALTH
# -----------------------------

@app.get("/api/health")
def health():
    return JSONResponse(
        content={"status": "ok"},
        media_type="application/json; charset=utf-8",
    )


# -----------------------------
# CORE ROUTERS
# -----------------------------

app.include_router(chat_router)
app.include_router(models_router)
app.include_router(profiles_router)
app.include_router(settings_router)
app.include_router(library_router)
app.include_router(memory_router)
app.include_router(agents_router)
app.include_router(tools_router)
app.include_router(project_patch_router)
app.include_router(agent_supervisor_router)
app.include_router(run_history_router)


# -----------------------------
# OPTIONAL RUNTIME ROUTERS
# -----------------------------

if browser_runtime_router:
    app.include_router(browser_runtime_router)

if desktop_runtime_router:
    app.include_router(desktop_runtime_router)


# -----------------------------
# STARTUP
# -----------------------------

@app.on_event("startup")
async def startup_event():
    print("Jarvis backend started")


# -----------------------------
# SHUTDOWN
# -----------------------------

@app.on_event("shutdown")
async def shutdown_event():
    print("Jarvis backend stopped")
