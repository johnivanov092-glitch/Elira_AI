from pathlib import Path
import logging

# Load .env and .env.local from backend/ directory so service config (SEARXNG_URL etc.)
# are available whether the server is started via Elira.bat or manually.
# existing_envs are not overridden — OS-level env vars always win.
_backend_dir = Path(__file__).resolve().parent.parent  # backend/
try:
    from dotenv import load_dotenv
    load_dotenv(_backend_dir / ".env",       override=False)
    load_dotenv(_backend_dir / ".env.local", override=False)
except ImportError:
    pass  # python-dotenv not installed — fall back to OS env only

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from app.api.routes.registry import ALL_ROUTERS
from app.application.elira_memory.service import init_db
from app.application.runtime.status import init_runtime_state
from app.core.auth import is_authorized

# Centralized logging config (FIX-16). basicConfig is a no-op if the host
# (uvicorn / pytest) already configured the root logger, so it never clobbers
# an existing setup — it just ensures the app's own logs have a format + level
# when started bare.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Elira AI API")


# Global safety net (FIX-16): any UNhandled route exception returns a generic 500
# body — never the raw exception text (which can leak internal paths/state) — and
# logs the full traceback server-side. FastAPI keeps handling HTTPException (404 /
# 413 / etc.) via its own handler, so those still return their intended detail.
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"ok": False, "error": "internal server error"}, status_code=500)

# ── Auth gate ───────────────────────────────────────────────────────────────
# Loopback (Tauri shell + dev browser) is trusted and needs no token. Any
# non-local caller (LAN / mobile) must present a bearer token. Registered
# BEFORE CORS so CORS stays the OUTERMOST layer and still attaches headers to
# 401 responses (the last-added middleware is outermost in Starlette).
_AUTH_OPEN_PATHS = frozenset({"/health"})


@app.middleware("http")
async def _auth_guard(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in _AUTH_OPEN_PATHS:
        return await call_next(request)
    client_host = request.client.host if request.client else None
    if not is_authorized(client_host, request.headers.get("authorization")):
        return JSONResponse(
            {"detail": "Unauthorized: API token required for non-local access"},
            status_code=401,
        )
    return await call_next(request)


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

init_db()
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

try:
    from app.application.task_planner.service import (
        recover_stale_tasks,
        start_task_recovery_scheduler,
    )
    recover_stale_tasks()
    # Self-heal stale tasks on a long-lived server without waiting for a restart.
    start_task_recovery_scheduler()
except Exception as exc:
    logger.warning("task planner startup recovery failed: %s", exc)

# Living Persona step C — scheduled-proactivity daemon. No-op unless the master
# switch (feature flag `proactive`) is on AND the scheduled trigger is approved;
# fully fail-safe. Evaluates a once-per-day check-in at the configured time.
try:
    from app.application.persona.proactive import start_proactive_scheduler

    start_proactive_scheduler()
except Exception as exc:
    logger.warning("proactive scheduler startup failed: %s", exc)

@app.get("/health")
def health():
    return {"status": "ok", "service": "elira-ai-api"}
