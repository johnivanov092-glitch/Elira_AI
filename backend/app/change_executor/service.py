"""Executor service entrypoint — runs as the `elira-change-exec` principal, outside the
main backend, from executor-owned code/venv.

Startup: VERIFY isolation (fail-closed) → init the private store → mint a per-boot IPC
bearer token into an executor-owned token-file → start the dedicated-bot poller and the
periodic sweep → serve the two-call loopback IPC bound HARD to 127.0.0.1.

Re-negotiated IPC boundary (see the contract): the main backend and `run_bash` share one
OS principal, so no transport can exclude `run_bash`; the bearer token separates OTHER
local users, and the real guarantee is that `request_plan` cannot change a host (rate-
limited read-only inspect + a Telegram the human must approve). The IPC never returns a
token/argv/key/binding. Stdlib only.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ipc, preflight, telegram
from . import store as cs

logger = logging.getLogger(__name__)

_MAX_BODY = 4096          # request body cap — the two calls carry tiny payloads
_SWEEP_INTERVAL = 30.0
_BIND_HOST = "127.0.0.1"  # HARD-CODED — never a parameter, so a remote bind is impossible


def _sweep_loop(stop=lambda: False, interval: float = _SWEEP_INTERVAL) -> None:
    while not stop():
        try:
            cs.expire_pending()
            cs.sweep_stale_applying()
        except Exception as exc:  # noqa: BLE001
            logger.warning("change executor sweep error: %s", exc)
        time.sleep(interval)


def _auth_ok(auth_header: str | None, token: str) -> bool:
    """Constant-time bearer check."""
    if not auth_header:
        return False
    parts = str(auth_header).split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return secrets.compare_digest(parts[1].strip(), token)


def handle_request(path: str, auth_header: str | None, body: bytes | None, *, token: str,
                   sender, rate_limiter, registry_path: str | None,
                   runner=None) -> tuple[int, dict]:
    """Pure IPC request handler (testable without a socket). A missing/wrong token → 401
    and NO engine call. Body must be <=4 KiB JSON; only /request_plan and /get_status."""
    if not _auth_ok(auth_header, token):
        return 401, {"ok": False, "error": "unauthorized"}
    if body is not None and len(body) > _MAX_BODY:
        return 413, {"ok": False, "error": "body_too_large"}
    try:
        payload = json.loads(body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError
    except Exception:  # noqa: BLE001
        return 400, {"ok": False, "error": "bad_request"}
    if path == "/request_plan":
        return 200, ipc.request_plan(str(payload.get("target_id") or ""), sender=sender,
                                     registry_path=registry_path, rate_limiter=rate_limiter,
                                     runner=runner)
    if path == "/get_status":
        return 200, ipc.get_status(str(payload.get("change_run_id") or ""))
    return 404, {"ok": False, "error": "unknown_method"}


def _make_handler(token, sender, rate_limiter, registry_path, runner=None):
    class _Handler(BaseHTTPRequestHandler):
        timeout = 10       # bound how long a slow client may hold a worker thread

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802
            # Authenticate BEFORE reading any body — an unauthenticated (or oversized)
            # request is rejected without consuming its payload, so it can't tie up a thread.
            if not _auth_ok(self.headers.get("Authorization"), token):
                return self._send(401, {"ok": False, "error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(400, {"ok": False, "error": "bad_request"})
            if n < 0 or n > _MAX_BODY:
                return self._send(413, {"ok": False, "error": "body_too_large"})
            body = self.rfile.read(n) if n else b"{}"
            code, result = handle_request(self.path, self.headers.get("Authorization"), body,
                                          token=token, sender=sender, rate_limiter=rate_limiter,
                                          registry_path=registry_path, runner=runner)
            self._send(code, result)

        def log_message(self, *args):    # never log request bodies / auth headers
            return

    return _Handler


def _mint_token_file() -> str:
    path = str(os.environ.get("ELIRA_CHANGE_IPC_TOKEN_FILE", "")).strip()
    if not path:
        raise RuntimeError("ELIRA_CHANGE_IPC_TOKEN_FILE not set")
    token = secrets.token_urlsafe(32)
    if os.name != "nt":
        # Create OWNER-ONLY (0o600) BEFORE writing the token — no world-readable window —
        # then widen to 0o640 so the main group can read it.
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, token.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(path, 0o640)
    else:
        # On Windows the token-file ACL (executor writes, main-group reads, others none) is
        # set on its directory by provisioning; preflight verifies that dir is not writable
        # by others. The file inherits that dir's ACL.
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(token)
    return token


def run(*, port: int | None = None) -> None:
    registry_path = os.environ.get("ELIRA_CHANGE_REGISTRY_PATH")
    preflight.require_isolated(registry_path=registry_path)   # refuse to run unless isolated
    cs.init_db()
    token = _mint_token_file()
    sender = telegram.ChangeApprovalSender()
    rate = ipc.RateLimiter(max_calls=int(os.environ.get("ELIRA_CHANGE_PLAN_RATE", "5")),
                           window_seconds=60.0)
    port = int(port if port is not None else os.environ.get("ELIRA_CHANGE_IPC_PORT", "8790"))
    threading.Thread(target=telegram.poll_loop, kwargs={"registry_path": registry_path},
                     daemon=True, name="change-bot").start()
    threading.Thread(target=_sweep_loop, daemon=True, name="change-sweep").start()
    server = ThreadingHTTPServer((_BIND_HOST, port), _make_handler(token, sender, rate, registry_path))
    logger.info("change executor IPC on %s:%s", _BIND_HOST, port)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
