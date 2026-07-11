"""Main-backend client for the change executor's loopback IPC.

This lives in the MAIN backend (NOT the executor TCB), so it may use the app's normal
deps. It reads the executor-written bearer token file (ACL: executor writes, main reads)
and calls the two-call loopback IPC. It can never approve or apply — it only requests a
plan (which the executor turns into a Telegram approval the human must act on) and reads
the executor's already-capped status. The token is sent as a bearer header, never logged.
"""
from __future__ import annotations

import os

import requests

_TIMEOUT = 120     # request_plan does a fresh inspect + Telegram send; get_status is fast


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


def _url(path: str) -> str:
    port = str(os.environ.get("ELIRA_CHANGE_IPC_PORT", "8790")).strip()
    return f"http://127.0.0.1:{port}{path}"


def _post(path: str, payload: dict) -> dict:
    try:
        resp = requests.post(_url(path), json=payload,
                             headers={"Authorization": f"Bearer {_token()}"}, timeout=_TIMEOUT)
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise ChangeExecutorUnavailable(f"change executor IPC unreachable: {exc}") from exc


def request_plan(target_id: str) -> dict:
    """Ask the executor to plan a change for *target_id*. Returns only {ok, change_run_id,
    status} — the executor never returns a token/argv/key/binding."""
    return _post("/request_plan", {"target_id": str(target_id or "")})


def get_status(change_run_id: str) -> dict:
    """Read the executor's capped status/evidence for a change run."""
    return _post("/get_status", {"change_run_id": str(change_run_id or "")})
