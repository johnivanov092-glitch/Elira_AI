"""Main-backend client for the change executor's loopback IPC.

This lives in the MAIN backend (NOT the executor TCB), so it may use the app's normal
deps. It reads the executor-written bearer token file (ACL: executor writes, main reads)
and calls the bounded loopback IPC. Remote work requests a plan which the executor turns
into a Telegram approval. Locally approved or policy-approved Tauri work may request one atomic plan+apply; the
executor still owns capabilities, target binding, argv, snapshot and verification. The
token is sent as a bearer header, never logged.
"""
from __future__ import annotations

import os

import requests

_PLAN_TIMEOUT = 120
_LOCAL_APPLY_TIMEOUT = 900   # aligns with the kernel's net.outbound tool budget
_STATUS_TIMEOUT = 30

# Local change execution is intentionally narrower than the executor registry. The model/UI can
# name only these reviewed operations; host/unit/path/argv remain executor-owned.
LOCAL_CHANGE_TARGETS = frozenset({
    "ai-server-netdata",
    "ai-server-netdata-config",
    "phase6-sqlite-canary",
})


class ChangeExecutorUnavailable(RuntimeError):
    """The change executor IPC could not be reached / its token is unreadable."""


def _token() -> str:
    path = str(os.environ.get("ELIRA_CHANGE_IPC_TOKEN_FILE", "")).strip()
    if not path:
        raise ChangeExecutorUnavailable("ELIRA_CHANGE_IPC_TOKEN_FILE not set")
    try:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError as exc:
        raise ChangeExecutorUnavailable(f"IPC token file unreadable: {exc}") from exc
    if not token:
        raise ChangeExecutorUnavailable("IPC token file is empty")
    return token


def _ipc_port() -> int:
    """The loopback IPC port as a STRICT integer in 1..65535. A raw env string interpolated
    into the URL is an exfiltration vector: `ELIRA_CHANGE_IPC_PORT=8790@attacker.example`
    would make the URL host `attacker.example` (the `127.0.0.1:8790` becomes userinfo) and
    the bearer would be sent there. Rejecting anything that is not a plain ASCII integer
    keeps the host pinned to 127.0.0.1. Fail-closed on anything malformed."""
    raw = str(os.environ.get("ELIRA_CHANGE_IPC_PORT", "8790")).strip()
    if not (raw.isascii() and raw.isdigit()):          # no '@', '.', sign, whitespace, unicode digits
        raise ChangeExecutorUnavailable(f"ELIRA_CHANGE_IPC_PORT is not a plain integer: {raw!r}")
    port = int(raw)
    if not 1 <= port <= 65535:
        raise ChangeExecutorUnavailable(f"ELIRA_CHANGE_IPC_PORT out of range: {port}")
    return port


def _session() -> requests.Session:
    """A session that ignores proxy/netrc env (`trust_env=False`) so `HTTP_PROXY` &co can't
    route the bearer off-box, and that will be called with `allow_redirects=False` so a 3xx
    can't bounce the bearer to another host."""
    s = requests.Session()
    s.trust_env = False
    return s


def _post(path: str, payload: dict, *, timeout: int) -> dict:
    url = f"http://127.0.0.1:{_ipc_port()}{path}"       # host pinned; port strictly validated above
    sess = _session()
    try:
        resp = sess.post(url, json=payload, headers={"Authorization": f"Bearer {_token()}"},
                         timeout=timeout, allow_redirects=False)
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise ChangeExecutorUnavailable(f"change executor IPC unreachable: {exc}") from exc
    finally:
        sess.close()


def request_plan(target_id: str) -> dict:
    """Ask the executor to plan a change for *target_id*. Returns only {ok, change_run_id,
    status} — the executor never returns a token/argv/key/binding."""
    return _post("/request_plan", {"target_id": str(target_id or "")}, timeout=_PLAN_TIMEOUT)


def apply_local(target_id: str) -> dict:
    """Run one reviewed target through the executor's local Tauri policy path."""
    normalized = str(target_id or "").strip()
    if normalized not in LOCAL_CHANGE_TARGETS:
        return {"ok": False, "error": "target_not_allowed"}
    return _post("/apply_local", {"target_id": normalized}, timeout=_LOCAL_APPLY_TIMEOUT)


def get_status(change_run_id: str) -> dict:
    """Read the executor's capped status/evidence for a change run."""
    return _post("/get_status", {"change_run_id": str(change_run_id or "")},
                 timeout=_STATUS_TIMEOUT)
