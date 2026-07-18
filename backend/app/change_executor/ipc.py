"""Executor-side IPC surface — three bounded calls the main backend may make:
`request_plan(target_id)`, `apply_local(target_id)`, and `get_status(change_run_id)`.

Non-negotiable: the IPC NEVER returns a raw capability token, the SSH argv, a key/path,
or the registry binding. `request_plan` returns only `{change_run_id, status}`; the
executor itself does the plan, stores the capability, and sends the Telegram buttons (the
raw tokens go only into callback_data). `get_status` returns a tight capped whitelist —
no raw command output and no service/internal paths (e.g. FragmentPath is dropped).

`apply_local` is the existing Tauri/bypass path: it performs plan + capability consume +
apply entirely inside the executor and returns only the safe id/status envelope. It never
sends Telegram and never returns a capability. No generic `approve`/`reject`/`resolve`/
argv/host/unit crosses this boundary. Both mutation entrypoints are rate-limited.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable, Protocol

from . import engine, policy
from . import store as cs
from .registry import RegistryError

logger = logging.getLogger(__name__)

_CAP = 200
# get_status evidence projection — safe systemd status fields only. NO FragmentPath (a
# path), NO stderr / raw output.
_STATUS_RESULT_SCALARS = ("outcome", "verdict", "reason")
_STATUS_CONFIG_SCALARS = ("config_id", "before_sha256", "after_sha256")
_STATUS_DATABASE_SCALARS = (
    "database_id", "migration_id", "backup_sha256", "schema_sha256",
)
_STATUS_DATABASE_INTS = ("before_user_version", "after_user_version", "row_count")
_STATUS_FIELD_KEYS = ("id", "load_state", "active_state", "sub_state", "unit_file_state",
                      "main_pid", "exec_main_status", "n_restarts")


class ApprovalSender(Protocol):
    def is_configured(self) -> bool: ...
    def send_approval(self, *, change_run_id: str, target_id: str, snapshot: dict,
                      approve_token: str, reject_token: str) -> None: ...


class RateLimiter:
    """Fixed-window plan rate limit (executor-owned). Thread-safe — the ThreadingHTTPServer
    can call allow() concurrently, so the prune/check/append is guarded by a Lock (otherwise
    parallel callers could both pass the len() check and exceed the limit)."""

    def __init__(self, max_calls: int, window_seconds: float, clock: Callable[[], float] = time.time):
        self.max, self.window, self.clock = max_calls, window_seconds, clock
        self._calls: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = self.clock()
        with self._lock:
            self._calls = [t for t in self._calls if now - t < self.window]
            if len(self._calls) >= self.max:
                return False
            self._calls.append(now)
            return True


def request_plan(target_id: str, *, sender: ApprovalSender, registry_path: str | None = None,
                 rate_limiter: RateLimiter | None = None, runner: Callable | None = None) -> dict[str, Any]:
    """Executor-side plan request. Returns ONLY {ok, change_run_id, status} on success —
    never a token/argv/key/binding. Fails closed if the sender is not configured (no
    ChangeRun, no send)."""
    if rate_limiter is not None and not rate_limiter.allow():
        return {"ok": False, "error": "rate_limited"}
    if not sender.is_configured():          # no bot token / approver allowlist → fail-closed
        return {"ok": False, "error": "not_configured"}
    try:
        plan = engine.plan(target_id, registry_path=registry_path,
                           **({"runner": runner} if runner is not None else {}))
    except engine.PreconditionError:
        return {"ok": False, "error": "precondition_not_met"}
    except cs.ActiveTargetConflict:
        return {"ok": False, "error": "active_change_exists"}
    except RegistryError:
        return {"ok": False, "error": "unknown_or_invalid_target"}
    try:
        sender.send_approval(change_run_id=plan["change_run_id"], target_id=target_id,
                             snapshot=plan["snapshot"], approve_token=plan["approve_token"],
                             reject_token=plan["reject_token"])
    except Exception:  # noqa: BLE001
        # A failed send must NOT leave the target locked in pending_approval: CAS to a
        # terminal delivery_failed and invalidate the capabilities (frees the target now).
        logger.warning("itops change: approval send failed for %s", plan["change_run_id"])
        cs.mark_delivery_failed(change_run_id=plan["change_run_id"])
        return {"ok": False, "error": "send_failed", "change_run_id": plan["change_run_id"],
                "status": "delivery_failed"}
    # SUCCESS: only the id + status leave the executor. No token/argv/binding.
    return {"ok": True, "change_run_id": plan["change_run_id"], "status": "pending_approval"}


def apply_local(target_id: str, *, registry_path: str | None = None,
                rate_limiter: RateLimiter | None = None,
                runner: Callable | None = None) -> dict[str, Any]:
    """Plan and apply one server-registered change for the local Tauri bypass path.

    The raw one-time capability stays inside this function. The caller receives only a
    bounded status envelope; target resolution, snapshot, drift checks, apply and
    post-check remain executor-owned. Telegram is deliberately not involved.
    """
    if rate_limiter is not None and not rate_limiter.allow():
        return {"ok": False, "error": "rate_limited"}
    # Local bypass is granted only to targets with an explicit runtime-owned safety
    # profile. A newly added registry target does not inherit auto-apply by accident.
    if policy.decide_approval(
        "bypass",
        "local",
        policy.evidence_for_registered_target(str(target_id or "").strip()),
    ) != policy.AUTO:
        return {"ok": False, "error": "approval_required"}
    try:
        plan = engine.plan(target_id, registry_path=registry_path,
                           **({"runner": runner} if runner is not None else {}))
    except engine.PreconditionError:
        return {"ok": False, "error": "precondition_not_met"}
    except cs.ActiveTargetConflict:
        return {"ok": False, "error": "active_change_exists"}
    except RegistryError:
        return {"ok": False, "error": "unknown_or_invalid_target"}

    change_run_id = plan["change_run_id"]
    capability_hash = hashlib.sha256(plan["approve_token"].encode("utf-8")).hexdigest()
    claimed = cs.consume_capability(
        capability_hash=capability_hash,
        approver="local:bypass",
        apply_deadline_seconds=engine.APPLY_DEADLINE_SECONDS,
    )
    if claimed is None:
        # This should be unreachable immediately after plan. Free a still-pending target
        # rather than leave a local failure locked until the periodic expiry sweep.
        cs.mark_delivery_failed(change_run_id=change_run_id)
        current = cs.get_change_run(change_run_id)
        return {
            "ok": False,
            "error": "local_claim_failed",
            "change_run_id": change_run_id,
            "status": (current or {}).get("change_run_status", "delivery_failed"),
        }

    status = engine.apply(
        change_run_id,
        registry_path=registry_path,
        **({"runner": runner} if runner is not None else {}),
    )
    return {"ok": status == "applied", "change_run_id": change_run_id, "status": status}


def _project_evidence_result(raw_json: str) -> dict[str, Any]:
    try:
        d = json.loads(raw_json)
    except (ValueError, TypeError):
        return {}
    if not isinstance(d, dict):
        return {}
    out: dict[str, Any] = {}
    for k in _STATUS_RESULT_SCALARS:
        v = d.get(k)
        if isinstance(v, str):
            out[k] = v[:_CAP]
    for k in _STATUS_CONFIG_SCALARS:
        v = d.get(k)
        if isinstance(v, str):
            out[k] = v[:_CAP]
    for k in _STATUS_DATABASE_SCALARS:
        v = d.get(k)
        if isinstance(v, str):
            out[k] = v[:_CAP]
    for k in _STATUS_DATABASE_INTS:
        v = d.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    if isinstance(d.get("rollback_attempted"), bool):
        out["rollback_attempted"] = d["rollback_attempted"]
    fields = d.get("fields")
    if isinstance(fields, dict):
        out["fields"] = {k: fields[k] for k in _STATUS_FIELD_KEYS
                         if isinstance(fields.get(k), (str, int)) and not isinstance(fields.get(k), bool)}
    return out


def get_status(change_run_id: str) -> dict[str, Any]:
    """Read-only status for the UI — a tight capped whitelist. No snapshot/argv/binding/
    approver, no raw output, no service paths."""
    cr = cs.get_change_run(change_run_id)
    if cr is None:
        return {"ok": False, "error": "not_found"}
    evidence = [{"operation": e["operation"], "exit_status": e["exit_status"],
                 "captured_at": e["captured_at"], "result": _project_evidence_result(e["result"])}
                for e in cs.list_change_evidence(change_run_id)]
    return {"ok": True, "change_run_id": change_run_id, "unit": cr["unit"],
            "operation": cr["operation"], "status": cr["change_run_status"],
            "verdict": cr["verdict"], "created_at": cr["created_at"],
            "updated_at": cr["updated_at"], "evidence": evidence}
