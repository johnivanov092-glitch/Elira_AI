"""W6 per-domain politeness budget for outbound web fetches.

One process-wide gate: consecutive requests to the SAME registrable domain
(eTLD+1) are spaced at least MIN_INTERVAL_S apart — a crawl over one site
(sitemap discovery → selective web_fetch(store)) never hammers the host.
Different domains are unaffected. The wait is bounded (never longer than
MIN_INTERVAL_S) and thread-safe.

Deliberately simple: no queues, no token buckets — the tool surface is already
bounded (URL caps, time budgets), politeness only smooths the burst shape.
"""
from __future__ import annotations

import threading
import time

from app.application.web_evidence.freshness import registrable_domain

MIN_INTERVAL_S = 1.0

_lock = threading.Lock()
_last_hit: dict[str, float] = {}
_MAX_TRACKED = 512           # bound the map; oldest half dropped on overflow


def politeness_wait(url: str) -> float:
    """Sleep so that two fetches to one registrable domain are ≥MIN_INTERVAL_S
    apart. Returns the seconds actually slept (0.0 for a new/idle domain)."""
    dom = registrable_domain(url)
    if not dom:
        return 0.0
    with _lock:
        now = time.monotonic()
        prev = _last_hit.get(dom)
        wait = max(0.0, MIN_INTERVAL_S - (now - prev)) if prev is not None else 0.0
        _last_hit[dom] = now + wait     # reserve the slot for THIS request
        if len(_last_hit) > _MAX_TRACKED:
            for k in sorted(_last_hit, key=_last_hit.get)[: _MAX_TRACKED // 2]:
                _last_hit.pop(k, None)
    if wait > 0:
        time.sleep(min(wait, MIN_INTERVAL_S))
    return wait
