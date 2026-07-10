"""Scoped Read-Only SSH v1 — run-scoped operation scope.

A diagnostic run is bound to ONE saved connection profile with a read-only,
time-limited scope. While a run has a bound scope the unified executor
(``agent_kernel.executor.execute_tool``) turns it into a hard capability
allowlist: ONLY ``tool_search`` (discovery) and ``itops_ssh_healthcheck`` may
run — every other tool (ssh_*, run_bash, filesystem, browser, change tools) is
blocked at the single dispatch path, so the model cannot escape the scope. And
``itops_ssh_healthcheck`` can NEVER run without a bound scope.

Design (mirrors ``deferred_tools.py``):
- State is in-memory, keyed by ``run_id``; concurrent runs are isolated.
- The scope is created ONLY by the server (POST /api/itops/diagnostics/start),
  never by the model — the model never chooses the profile_id.
- TTL is enforced lazily on read: an expired scope reads as absent (None).
- ``reserve_healthcheck`` is an atomic compare-and-set — the FIRST caller wins,
  every later call is refused, so a run performs at most one health check.
- Callers drop the scope with ``clear_scope`` when the run ends (finally/cancel/
  error); expiry drops it lazily. A dropped/expired scope => the run is no longer
  scoped and the read-only tool is blocked (fail-closed).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# One diagnostic run runs three trivial commands (~a minute); 300s is ample.
DEFAULT_TTL_SECONDS = 300
# A run cannot outlive its execution deadline (DEFAULT_MAX_EXECUTION_SECONDS = 600s),
# so an entry expired longer ago than this has NO live run and is safe to sweep. This
# bounds _SCOPES growth from abandoned /diagnostics/start calls that never streamed.
_SWEEP_GRACE_SECONDS = 900

_LOCK = threading.RLock()
# run_id -> mutable scope record. Presence of the key == the run is LOCKED DOWN
# (sticky capability allowlist); it is removed ONLY by clear_scope (the run's finally)
# or the abandoned-entry sweep — never by TTL. TTL only governs whether a LIVE scope
# exists (get_active_scope), which the one-shot health check requires.
_SCOPES: dict[str, dict] = {}


@dataclass(frozen=True)
class ScopeView:
    """Immutable snapshot handed to the executor gate (never the live dict)."""
    run_id: str
    profile_id: str
    mode: str
    expires_at: float
    healthcheck_used: bool


def _norm(run_id: str) -> str:
    return str(run_id or "").strip()


def bind_scope(run_id: str, profile_id: str, *, mode: str = "read_only",
               ttl_seconds: float = DEFAULT_TTL_SECONDS) -> ScopeView | None:
    """Bind *run_id* to a read-only diagnostic scope over *profile_id*.

    No-op (returns None) for an empty run_id / profile_id. Re-binding replaces the
    prior scope for that run (and resets healthcheck_used).
    """
    rid = _norm(run_id)
    pid = str(profile_id or "").strip()
    if not rid or not pid:
        return None
    with _LOCK:
        _sweep_locked()   # bound growth: drop long-dead abandoned entries
        _SCOPES[rid] = {
            "profile_id": pid,
            "mode": str(mode),
            "expires_at": time.time() + float(ttl_seconds),
            "healthcheck_used": False,
        }
        return _view(rid)


def _sweep_locked() -> None:
    """Drop entries whose run is long gone (expired more than the sweep grace ago).

    Caller holds _LOCK. Any live run has already been cleared by its finally or is
    still within its execution deadline, so an entry this stale has no live run and
    dropping it cannot lift a live run's lockdown."""
    now = time.time()
    for k in [k for k, v in _SCOPES.items() if now - v["expires_at"] > _SWEEP_GRACE_SECONDS]:
        _SCOPES.pop(k, None)


def _view(rid: str) -> ScopeView | None:
    s = _SCOPES.get(rid)
    if s is None:
        return None
    return ScopeView(rid, s["profile_id"], s["mode"], s["expires_at"], s["healthcheck_used"])


def get_active_scope(run_id: str) -> ScopeView | None:
    """The run's LIVE scope, or None if there is none or the TTL has expired.

    Used to authorize the one-shot health check (which needs a live scope). Expiry
    does NOT drop the entry — the run stays LOCKED DOWN (is_locked_down) until
    clear_scope, so TTL only tightens (blocks the health check), never loosens.
    """
    rid = _norm(run_id)
    if not rid:
        return None
    with _LOCK:
        s = _SCOPES.get(rid)
        if s is None or time.time() > s["expires_at"]:
            return None
        return _view(rid)


def is_locked_down(run_id: str) -> bool:
    """True if the run was bound to a diagnostic scope and not yet cleared —
    REGARDLESS of TTL. The capability allowlist is sticky: an expired TTL must never
    re-open the tools a scoped run was barred from. Cleared only by clear_scope
    (run finally/cancel/error) or the abandoned-entry sweep."""
    rid = _norm(run_id)
    if not rid:
        return False
    with _LOCK:
        return rid in _SCOPES


def is_scoped(run_id: str) -> bool:
    """True if the run currently has a live (non-expired) operation scope."""
    return get_active_scope(run_id) is not None


def reserve_healthcheck(run_id: str) -> bool:
    """Atomically claim the run's single health-check slot.

    Returns True exactly once per live scope (first caller wins); False if there
    is no live scope or the slot was already claimed. Reserving BEFORE dispatch
    means a failed dispatch still consumes the one attempt — retries need a new run.
    """
    rid = _norm(run_id)
    if not rid:
        return False
    with _LOCK:
        s = _SCOPES.get(rid)
        if s is None or time.time() > s["expires_at"]:
            return False
        if s["healthcheck_used"]:
            return False
        s["healthcheck_used"] = True
        return True


def clear_scope(run_id: str) -> None:
    """Drop a run's scope (call on every terminal exit: finally/cancel/error)."""
    rid = _norm(run_id)
    with _LOCK:
        _SCOPES.pop(rid, None)
