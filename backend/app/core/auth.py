"""API authentication — loopback is trusted, non-local access needs a token.

Security model (chosen 2026-06-14):

* Requests coming from the local machine (loopback IPs and the in-process
  Starlette test client) are trusted and need no token. This keeps the desktop
  Tauri shell and the dev browser frontend working with zero friction.
* Any non-loopback caller (LAN / mobile) must present a bearer token that
  matches the server token. This closes the unauthenticated-RCE vector when the
  backend is bound to ``0.0.0.0`` in mobile mode.

The token comes from ``ELIRA_API_TOKEN`` if set, otherwise it is generated once
and persisted to ``data/elira_api_token`` (never committed — runtime data).
Enforcement can be disabled with ``ELIRA_API_AUTH=off`` (for trusted setups).
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets

from app.core.config import DATA_DIR

logger = logging.getLogger(__name__)

_TOKEN_FILE = DATA_DIR / "elira_api_token"

# Hosts trusted without a token. ``testclient`` is the default host of
# fastapi.testclient.TestClient — it is in-process and not reachable over any
# network, so trusting it has no real-world attack surface. An empty host means
# the client could not be determined (ASGI without a peer) — treat as local.
_TRUSTED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient", ""})

_FALSE_VALUES = frozenset({"off", "0", "false", "no"})


def _load_or_create_token() -> str:
    """Return the configured token, persisting an auto-generated one if needed."""
    env_token = os.getenv("ELIRA_API_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        if _TOKEN_FILE.exists():
            stored = _TOKEN_FILE.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        generated = secrets.token_urlsafe(32)
        _TOKEN_FILE.write_text(generated, encoding="utf-8")
        logger.info("Generated new API token at %s", _TOKEN_FILE)
        return generated
    except OSError as exc:
        # Could not persist — fall back to an ephemeral per-process token so the
        # server still enforces *something* for non-local callers.
        logger.warning("Could not persist API token (%s); using ephemeral token", exc)
        return secrets.token_urlsafe(32)


# Resolved once at import; safe because token material is stable per process.
API_TOKEN: str = _load_or_create_token()


def auth_enabled() -> bool:
    """True unless ``ELIRA_API_AUTH`` is explicitly set to a false-y value."""
    return os.getenv("ELIRA_API_AUTH", "on").strip().lower() not in _FALSE_VALUES


def is_trusted_host(host: str | None) -> bool:
    """True if *host* is loopback / local and may skip token checks."""
    cleaned = (host or "").strip().lower()
    if cleaned in _TRUSTED_HOSTS:
        return True
    try:
        return ipaddress.ip_address(cleaned).is_loopback
    except ValueError:
        return False


def extract_bearer(auth_header: str | None) -> str:
    """Return the token from an ``Authorization`` header (Bearer or raw)."""
    if not auth_header:
        return ""
    parts = auth_header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return auth_header.strip()


def make_auth_middleware(open_paths):
    """Build the HTTP auth middleware (extracted from main.py so the 401 path is
    testable via ASGI without importing the whole app + its startup schedulers).
    Loopback callers pass; non-loopback callers need a valid token."""
    from starlette.responses import JSONResponse

    async def _auth_guard(request, call_next):
        if request.method == "OPTIONS" or request.url.path in open_paths:
            return await call_next(request)
        client_host = request.client.host if request.client else None
        if not is_authorized(client_host, request.headers.get("authorization")):
            return JSONResponse(
                {"detail": "Unauthorized: API token required for non-local access"},
                status_code=401,
            )
        return await call_next(request)

    return _auth_guard


def is_authorized(
    client_host: str | None,
    auth_header: str | None,
    *,
    token: str | None = None,
    enabled: bool | None = None,
) -> bool:
    """Decide whether a request is allowed.

    Loopback/local callers are always allowed. Everyone else must present a
    token matching *token* (defaults to the server :data:`API_TOKEN`). When
    enforcement is disabled, every request is allowed.
    """
    if enabled is None:
        enabled = auth_enabled()
    if not enabled:
        return True
    if is_trusted_host(client_host):
        return True
    presented = extract_bearer(auth_header)
    expected = API_TOKEN if token is None else token
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)
