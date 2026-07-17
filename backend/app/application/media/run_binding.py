"""Run → resource binding (R1) — the processing allowlist.

Mirrors ``agent_kernel.deferred_tools``: in-memory, keyed by ``run_id``, so
concurrent runs are isolated. A code-agent run is bound to the exact set of
resource ids that were attached to THIS run (after the route validated each
one's owner). ``resource_process`` trusts ONLY this binding — a resource id that
is unknown, owned by another session, or simply not attached to the current run
is never in the set, so processing fails closed before it ever loads bytes.

There is deliberately no bare "look up any resource by id" path exposed to the
model: resolution always goes id → is_bound(run_id, id) → store lookup.
"""
from __future__ import annotations

import itertools
import threading
from collections.abc import Iterable

_LOCK = threading.RLock()
# run_id -> set of resource ids attached to that run.
_BOUND: dict[str, set[str]] = {}
_GENERATIONS: dict[str, int] = {}
_GENERATION_COUNTER = itertools.count(1)


def _norm(run_id: str) -> str:
    return str(run_id or "").strip()


def bind_resources(run_id: str, resource_ids: Iterable[str]) -> set[str]:
    """Bind *resource_ids* to *run_id* (replacing any prior binding for the run).
    No-op (returns an empty set) for an empty run_id. Returns a copy of the set."""
    rid = _norm(run_id)
    ids = {str(r).strip() for r in resource_ids if str(r).strip()}
    if not rid:
        return set()
    with _LOCK:
        if ids:
            _BOUND[rid] = set(ids)
            _GENERATIONS[rid] = next(_GENERATION_COUNTER)
        else:
            _BOUND.pop(rid, None)
            _GENERATIONS.pop(rid, None)
        return set(ids)


def is_bound(run_id: str, resource_id: str) -> bool:
    """True iff *resource_id* is attached to *run_id*."""
    rid = _norm(run_id)
    res = str(resource_id or "").strip()
    if not rid or not res:
        return False
    with _LOCK:
        return res in _BOUND.get(rid, set())


def bound_resources(run_id: str) -> set[str]:
    """Copy of the resource ids bound to *run_id* (empty if none)."""
    rid = _norm(run_id)
    with _LOCK:
        return set(_BOUND.get(rid, set()))


def binding_generation(run_id: str, *, required_resource_id: str = "") -> int | None:
    """Return the current opaque generation, optionally only if a source is bound."""
    rid = _norm(run_id)
    required = str(required_resource_id or "").strip()
    if not rid:
        return None
    with _LOCK:
        current = _BOUND.get(rid)
        if current is None or (required and required not in current):
            return None
        return _GENERATIONS.get(rid)


def add_bound(run_id: str, resource_id: str, *, required_resource_id: str = "",
              required_generation: int | None = None) -> bool:
    """Atomically ADD a single id to a run's binding, preserving the existing set.

    Unlike :func:`bind_resources` (which REPLACES the whole set), this unions
    under the lock, so attaching a derived resource never drops the run's
    original inputs. A read-union-write via ``bind_resources`` would race a
    concurrent bind on the same run; this does not. Returns True on success,
    False for invalid input, when the run no longer has a live binding, or when
    *required_resource_id* is no longer in that binding, or its opaque generation
    changed. These checks atomically pin a derived result to the exact source run
    binding across concurrent replacement (including same-source ABA replacement).
    It deliberately never recreates a binding removed by :func:`clear_run`."""
    rid = _norm(run_id)
    res = str(resource_id or "").strip()
    required = str(required_resource_id or "").strip()
    if not rid or not res:
        return False
    with _LOCK:
        current = _BOUND.get(rid)
        if current is None or (required and required not in current):
            return False
        if required_generation is not None and _GENERATIONS.get(rid) != required_generation:
            return False
        current.add(res)
    return True


def clear_run(run_id: str) -> None:
    """Drop a run's resource binding (call when the run ends)."""
    rid = _norm(run_id)
    with _LOCK:
        _BOUND.pop(rid, None)
        _GENERATIONS.pop(rid, None)
