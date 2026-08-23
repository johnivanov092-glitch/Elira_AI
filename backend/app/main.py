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
from app.core.auth import make_auth_middleware

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


def _shutdown_integration_runtimes() -> None:
    """Release explicitly started child runtimes on a graceful backend stop."""
    from app.application.tool_providers import lsp_runtime, mcp_runtime

    mcp_runtime.stop_all_servers()
    lsp_runtime.stop_all_servers()


app.add_event_handler("shutdown", _shutdown_integration_runtimes)


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

# Middleware extracted to app.core.auth.make_auth_middleware so the 401 path is
# unit-testable via ASGI (see tests). Registered here BEFORE CORS so CORS stays
# outermost (last-added middleware is outermost in Starlette).
app.middleware("http")(make_auth_middleware(_AUTH_OPEN_PATHS))


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

from app.application.workflow_engine.runtime import seed_builtin_workflows
seed_builtin_workflows()

try:
    from app.application.workflows.db_path import get_workflow_db_path
    from app.application.workflows.request_lifecycle import (
        recover_incomplete_requests,
    )

    recovered_workflow_requests = recover_incomplete_requests(
        db_path=get_workflow_db_path()
    )
    if recovered_workflow_requests:
        logger.warning(
            "recovered %s incomplete workflow request(s)",
            recovered_workflow_requests,
        )
except Exception as exc:
    logger.warning("workflow request startup recovery failed: %s", exc)

# Workflow-owned interval triggers replace the old separate Pipelines control
# plane. Tests never start daemon schedulers; production starts it with the
# backend and manages it through runtime_control.
try:
    import sys as _sys

    if "pytest" not in _sys.modules:
        from app.application.workflows.triggers import start_scheduler

        start_scheduler()
except Exception as exc:
    logger.warning("workflow trigger scheduler startup failed: %s", exc)

from app.application.tool_registry.runtime import seed_builtin_tools
seed_builtin_tools()

# IT Operations metadata starts with the runtime. The portable vault stays locked;
# create/unlock/recovery are explicit workflow UI operations and never depend on
# Windows Credential Manager or the Windows account token.
try:
    from app.application.it_ops.startup import itops_startup

    itops_startup()
except Exception as exc:
    logger.warning("itops startup failed: %s", exc)

# Server drift detector — probe the live llama-server (/props) on start + daily,
# alert when the active model / context window drifts from what docs recorded.
# Read-only + fail-safe; pytest-guarded and killable via ELIRA_DRIFT_CHECK=0.
try:
    from app.application.drift.runtime import start_drift_scheduler

    start_drift_scheduler()
except Exception as exc:
    logger.warning("drift detector startup failed: %s", exc)

@app.get("/health")
def health():
    return {"status": "ok", "service": "elira-ai-api"}
