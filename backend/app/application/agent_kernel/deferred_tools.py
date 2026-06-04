"""P10.1 — run-scoped deferred tool activation.

Foundation for Deferred Tool Search. A run opts into deferred mode by
establishing a run-scoped allowlist of *activated* tools. While a run is in
deferred mode, the unified executor (``agent_kernel.executor.execute_tool``)
blocks any tool not in that run's active set — at the single dispatch path,
before the provider — even if the model names an unactivated tool by guessing.

Design:
- State is in-memory and keyed by ``run_id``; concurrent runs are isolated.
- A run is "deferred" ONLY after an explicit ``enable_deferred_tools`` call.
  Runs that never opt in have no entry here and are completely unaffected
  (inert / back-compat) — the executor enforcement is a no-op for them.
- ``activate_tools`` adds to an already-deferred run's set; it never silently
  flips a non-deferred run into deferred mode (avoids accidental lockdown).
- Activation grants *visibility* only. It does NOT bypass policy, scopes, or
  approvals — those gates still run in the executor after this check.
- Callers clear a run's entry with ``clear_run`` when the run ends.
"""
from __future__ import annotations

import threading
from collections.abc import Iterable

_LOCK = threading.RLock()
# run_id -> set of activated tool names. Presence of the key == deferred mode on.
_ACTIVE: dict[str, set[str]] = {}


def _norm(run_id: str) -> str:
    return str(run_id or "").strip()


def enable_deferred_tools(run_id: str, base_tools: Iterable[str] = ()) -> set[str]:
    """Put *run_id* into deferred mode, seeding its active set with *base_tools*.

    Idempotent in the sense that re-enabling resets the active set to *base_tools*.
    Returns a copy of the resulting active set. No-op for an empty run_id.
    """
    rid = _norm(run_id)
    if not rid:
        return set()
    seed = {str(t).strip() for t in base_tools if str(t).strip()}
    with _LOCK:
        _ACTIVE[rid] = set(seed)
        return set(seed)


def activate_tools(run_id: str, names: Iterable[str]) -> set[str]:
    """Add *names* to an already-deferred run's active set.

    No-op (returns an empty set) if the run is not in deferred mode — deferred
    mode must be entered explicitly via ``enable_deferred_tools``. Returns a copy
    of the resulting active set.
    """
    rid = _norm(run_id)
    add = {str(n).strip() for n in names if str(n).strip()}
    if not rid:
        return set()
    with _LOCK:
        current = _ACTIVE.get(rid)
        if current is None:
            return set()
        current.update(add)
        return set(current)


def is_deferred_run(run_id: str) -> bool:
    """True if *run_id* has opted into deferred tool mode."""
    rid = _norm(run_id)
    if not rid:
        return False
    with _LOCK:
        return rid in _ACTIVE


def is_tool_active(run_id: str, tool_name: str) -> bool:
    """True if *tool_name* is activated for *run_id*."""
    rid = _norm(run_id)
    if not rid:
        return False
    with _LOCK:
        return str(tool_name) in _ACTIVE.get(rid, set())


def get_active_tools(run_id: str) -> set[str]:
    """Copy of the active set for *run_id* (empty set if not deferred)."""
    rid = _norm(run_id)
    with _LOCK:
        return set(_ACTIVE.get(rid, set()))


def clear_run(run_id: str) -> None:
    """Drop a run's deferred state (call when the run ends)."""
    rid = _norm(run_id)
    with _LOCK:
        _ACTIVE.pop(rid, None)
