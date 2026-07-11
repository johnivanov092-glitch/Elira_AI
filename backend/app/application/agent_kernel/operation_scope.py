"""Scoped Read-Only SSH — run-scoped operation scope.

A diagnostic run is bound to ONE saved connection profile with a read-only,
time-limited scope that permits exactly ONE adapter tool (chosen server-side from
a fixed adapter→tool table — the model/client never names the tool). While a run
has a bound scope the unified executor (``agent_kernel.executor.execute_tool``)
turns it into a hard capability allowlist: ONLY ``tool_search`` (discovery) and
that one ``allowed_tool`` may run — every other tool (including other itops
adapters, ssh_*, run_bash, filesystem, browser, change tools) is blocked at the
single dispatch path, so the model cannot escape the scope. The allowlist is the
exact bound tool name, NOT "any itops tool", so a future itops tool is never
auto-runnable from an old scope. And an itops adapter tool can NEVER run without a
bound scope naming it.

Design (mirrors ``deferred_tools.py``):
- State is in-memory, keyed by ``run_id``; concurrent runs are isolated.
- The scope is created ONLY by the server (POST /api/itops/diagnostics/start),
  never by the model — the model never chooses the profile_id or the tool.
- TTL is enforced lazily on read: an expired scope reads as absent for the tool
  call (``get_active_scope`` None) but the lockdown stays sticky (``locked_tool``).
- ``reserve_operation`` is an atomic compare-and-set — the FIRST caller wins,
  every later call is refused, so a run performs at most one read-only operation.
- Callers drop the scope with ``clear_scope`` when the run ends (finally/cancel/
  error); expiry drops it lazily. A dropped/expired scope => the run is no longer
  scoped and the adapter tool is blocked (fail-closed).
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
# Diagnostic run_ids are server-minted with this prefix. Used to (a) fail CLOSED on a
# broken scope layer and (b) enforce one-shot streaming.
DIAG_RUN_PREFIX = "itops-diag-"
# How long a stream claim is remembered (well past any run's lifetime) so a repeat
# stream / resume is refused; bounds _CLAIMED growth.
_CLAIM_RETENTION_SECONDS = 3600

_LOCK = threading.RLock()
# run_id -> mutable scope record. Presence of the key == the run is LOCKED DOWN
# (sticky capability allowlist); it is removed ONLY by clear_scope (the run's finally)
# or the abandoned-entry sweep — never by TTL. TTL only governs whether a LIVE scope
# exists (get_active_scope), which the one-shot health check requires.
_SCOPES: dict[str, dict] = {}
# diagnostic run_id -> claim time. A diagnostic run_id may be streamed EXACTLY ONCE;
# this survives clear_scope so a repeat/parallel stream or a resume is refused.
_CLAIMED: dict[str, float] = {}


@dataclass(frozen=True)
class NetworkTarget:
    """Typed target for a target_kind='network' scope. cidr is the bound network;
    port_profile names the server-owned port set (ports resolved from it, never from
    the model)."""
    cidr: str
    port_profile: str


@dataclass(frozen=True)
class SystemdTarget:
    """Typed target for a target_kind='systemd_service' scope. The scope principal is
    the top-level profile_id (the enabled Linux asset); `unit` is the ONE systemd
    service unit the human selected — server-owned, never model-supplied."""
    unit: str


@dataclass(frozen=True)
class ScopeView:
    """Immutable snapshot handed to the executor gate (never the live dict)."""
    run_id: str
    mode: str
    expires_at: float
    operation_used: bool
    # The SINGLE adapter tool this scope permits (e.g. "itops_ssh_healthcheck" or
    # "itops_network_inventory"). The gate allows only tool_search + this exact tool —
    # a source==itops check is NOT used for allowlist membership, so a future itops
    # tool is never auto-runnable from an old scope.
    allowed_tool: str
    # Discriminated target. "ssh_profile" → profile_id; "network" → network;
    # "systemd_service" → profile_id (principal) + systemd.unit.
    target_kind: str = "ssh_profile"
    profile_id: str = ""
    network: NetworkTarget | None = None
    systemd: SystemdTarget | None = None


def _norm(run_id: str) -> str:
    return str(run_id or "").strip()


def bind_scope(run_id: str, profile_id: str, *, allowed_tool: str,
               mode: str = "read_only", ttl_seconds: float = DEFAULT_TTL_SECONDS) -> ScopeView | None:
    """Bind *run_id* to a read-only scope over *profile_id*, permitting exactly the
    one adapter tool *allowed_tool*.

    No-op (returns None) for an empty run_id / profile_id / allowed_tool. Re-binding
    replaces the prior scope (and resets operation_used).
    """
    rid = _norm(run_id)
    pid = str(profile_id or "").strip()
    tool = str(allowed_tool or "").strip()
    if not rid or not pid or not tool:
        return None
    with _LOCK:
        _sweep_locked()   # bound growth: drop long-dead abandoned entries
        _SCOPES[rid] = {
            "target_kind": "ssh_profile",
            "profile_id": pid,
            "network": None,
            "systemd": None,
            "mode": str(mode),
            "expires_at": time.time() + float(ttl_seconds),
            "operation_used": False,
            "allowed_tool": tool,
        }
        return _view(rid)


def bind_scope_network(run_id: str, *, cidr: str, port_profile: str, allowed_tool: str,
                       mode: str = "read_only", ttl_seconds: float = DEFAULT_TTL_SECONDS) -> ScopeView | None:
    """Bind *run_id* to a read-only NETWORK scope over the exact *cidr* + server-owned
    *port_profile*, permitting exactly the one adapter tool *allowed_tool*. Same sticky
    lockdown / one-op reserve machinery as an ssh_profile scope. No-op on empty args."""
    rid = _norm(run_id)
    c = str(cidr or "").strip()
    pp = str(port_profile or "").strip()
    tool = str(allowed_tool or "").strip()
    if not rid or not c or not pp or not tool:
        return None
    with _LOCK:
        _sweep_locked()
        _SCOPES[rid] = {
            "target_kind": "network",
            "profile_id": "",
            "network": {"cidr": c, "port_profile": pp},
            "systemd": None,
            "mode": str(mode),
            "expires_at": time.time() + float(ttl_seconds),
            "operation_used": False,
            "allowed_tool": tool,
        }
        return _view(rid)


def bind_scope_systemd(run_id: str, *, profile_id: str, unit: str, allowed_tool: str,
                       mode: str = "read_only", ttl_seconds: float = DEFAULT_TTL_SECONDS) -> ScopeView | None:
    """Bind *run_id* to a read-only SYSTEMD-SERVICE scope: the enabled Linux asset
    *profile_id* (the scope principal, validated by the gate) + the one selected *unit*,
    permitting exactly the one adapter tool *allowed_tool*. Same sticky lockdown /
    one-op reserve machinery as the other scopes. No-op on empty args."""
    rid = _norm(run_id)
    pid = str(profile_id or "").strip()
    u = str(unit or "").strip()
    tool = str(allowed_tool or "").strip()
    if not rid or not pid or not u or not tool:
        return None
    with _LOCK:
        _sweep_locked()
        _SCOPES[rid] = {
            "target_kind": "systemd_service",
            "profile_id": pid,
            "network": None,
            "systemd": {"unit": u},
            "mode": str(mode),
            "expires_at": time.time() + float(ttl_seconds),
            "operation_used": False,
            "allowed_tool": tool,
        }
        return _view(rid)


def _sweep_locked() -> None:
    """Drop entries whose run is long gone (expired more than the sweep grace ago),
    and claims older than the retention window.

    Caller holds _LOCK. Any live run has already been cleared by its finally or is
    still within its execution deadline, so an entry this stale has no live run and
    dropping it cannot lift a live run's lockdown."""
    now = time.time()
    for k in [k for k, v in _SCOPES.items() if now - v["expires_at"] > _SWEEP_GRACE_SECONDS]:
        _SCOPES.pop(k, None)
    for k in [k for k, t in _CLAIMED.items() if now - t > _CLAIM_RETENTION_SECONDS]:
        _CLAIMED.pop(k, None)


def claim_stream(run_id: str) -> bool:
    """Atomically claim a diagnostic run_id for its SINGLE stream. True the first
    time, False on every repeat — so a diagnostic run can never be streamed twice
    (parallel or sequential) or resumed. Without this, a finished stream's
    clear_scope could lift the lockdown while a second stream on the same run_id is
    still live, and a post-cleanup re-stream/resume would run with no scope at all."""
    rid = _norm(run_id)
    if not rid:
        return False
    with _LOCK:
        _sweep_locked()
        if rid in _CLAIMED:
            return False
        _CLAIMED[rid] = time.time()
        return True


def _view(rid: str) -> ScopeView | None:
    s = _SCOPES.get(rid)
    if s is None:
        return None
    net = s.get("network")
    sysd = s.get("systemd")
    return ScopeView(
        run_id=rid, mode=s["mode"], expires_at=s["expires_at"],
        operation_used=s["operation_used"], allowed_tool=s["allowed_tool"],
        target_kind=s.get("target_kind", "ssh_profile"), profile_id=s.get("profile_id", ""),
        network=NetworkTarget(net["cidr"], net["port_profile"]) if net else None,
        systemd=SystemdTarget(sysd["unit"]) if sysd else None)


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
    return locked_tool(run_id) is not None


def locked_tool(run_id: str) -> str | None:
    """The SINGLE adapter tool this locked-down run permits, REGARDLESS of TTL.
    None if the run is not locked down. The gate's sticky allowlist (tool_search +
    this tool) reads this so an expired TTL never re-opens other tools."""
    rid = _norm(run_id)
    if not rid:
        return None
    with _LOCK:
        s = _SCOPES.get(rid)
        return s["allowed_tool"] if s else None


def is_scoped(run_id: str) -> bool:
    """True if the run currently has a live (non-expired) operation scope."""
    return get_active_scope(run_id) is not None


def reserve_operation(run_id: str) -> bool:
    """Atomically claim the run's SINGLE read-only operation slot.

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
        if s["operation_used"]:
            return False
        s["operation_used"] = True
        return True


def clear_scope(run_id: str) -> None:
    """Drop a run's scope (call on every terminal exit: finally/cancel/error)."""
    rid = _norm(run_id)
    with _LOCK:
        _SCOPES.pop(rid, None)
