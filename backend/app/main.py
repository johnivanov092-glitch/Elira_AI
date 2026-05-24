import os
from pathlib import Path

# Load .env and .env.local from backend/ directory so API keys (TAVILY_API_KEY etc.)
# are available whether the server is started via Elira.bat or manually.
# existing_envs are not overridden — OS-level env vars always win.
_backend_dir = Path(__file__).resolve().parent.parent  # backend/
try:
    from dotenv import load_dotenv
    load_dotenv(_backend_dir / ".env",       override=False)
    load_dotenv(_backend_dir / ".env.local", override=False)
except ImportError:
    pass  # python-dotenv not installed — fall back to OS env only

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.registry import ALL_ROUTERS
from app.application.runtime.status import init_runtime_state

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

for router in ALL_ROUTERS:
    app.include_router(router)

init_runtime_state()

# Seed встроенных агентов в Agent Registry при старте
from app.application.agent_registry.runtime import seed_builtin_agents
from app.application.monitoring.runtime import seed_default_limits
from app.application.workflow_engine.runtime import seed_builtin_workflows
seed_builtin_agents()
seed_builtin_workflows()
seed_default_limits()

from app.application.tool_registry.runtime import seed_builtin_tools
seed_builtin_tools()

@app.get("/health")
def health():
    return {"status": "ok", "service": "elira-ai-api"}
