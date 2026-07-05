"""Server drift detector — reconcile authoritative live facts against what was
last recorded and alert when they drift.

The bug this closes: a doc/memory fact (e.g. "active model = Qwopus") silently
goes stale when the server changes under it, then gets cited as current. Here we
probe the live server on start + daily; when a fact changes we emit an event +
WARNING so the drift surfaces instead of rotting unseen.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any

from app.application.drift import probes as drift_probes
from app.application.drift import store as drift_store

logger = logging.getLogger(__name__)

_DAILY_SECONDS = 24 * 60 * 60
_stop = threading.Event()
_scheduler_started = False


def reconcile() -> dict[str, Any]:
    """Probe live facts, update the store, emit an event per drift.

    Returns {reachable, facts, drifts}. Never overwrites a known value with an
    unknown (server briefly unreachable) — that would fake a drift.
    """
    live = drift_probes.probe_live_facts()
    reachable = any(v is not None for v in live.values())
    facts: list[dict[str, Any]] = []
    drifts: list[dict[str, Any]] = []

    for key, value in live.items():
        if value is None:
            existing = drift_store.get_fact(key)
            facts.append(
                {"key": key, "value": existing["value"] if existing else None, "verified": False}
            )
            continue
        res = drift_store.upsert_fact(key, value)
        facts.append({"key": key, "value": value, "verified": True})
        if res["changed"]:
            drifts.append(res)
            _alert(key, res["previous"], value)

    return {"reachable": reachable, "facts": facts, "drifts": drifts}


def _alert(key: str, previous: str | None, current: str | None) -> None:
    label = drift_probes.FACT_LABELS.get(key, key)
    logger.warning("drift: %s changed: %r -> %r", label, previous, current)
    try:
        from app.application.event_bus.runtime import emit_event

        emit_event(
            event_type="server.fact.drift",
            payload={"key": key, "label": label, "previous": previous, "current": current},
            source_agent_id="drift-detector",
        )
    except Exception as exc:  # alerting is best-effort — never break reconcile
        logger.info("drift: emit_event failed: %s", exc)


def get_status() -> dict[str, Any]:
    return {"facts": drift_store.all_facts()}


def get_alerts() -> dict[str, Any]:
    """Active (unacknowledged) drifts — what the UI badge counts."""
    drifts = drift_store.active_drifts()
    return {"count": len(drifts), "drifts": drifts}


def acknowledge() -> dict[str, Any]:
    return {"acknowledged": drift_store.acknowledge_all()}


def start_drift_scheduler() -> None:
    """Reconcile once on start, then daily, in a daemon thread.

    Guarded so pytest (which imports app.main) never fires network calls, and so
    the whole thing can be disabled with ELIRA_DRIFT_CHECK=0.
    """
    global _scheduler_started
    if _scheduler_started or "pytest" in sys.modules:
        return
    if os.getenv("ELIRA_DRIFT_CHECK", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    _scheduler_started = True

    def _loop() -> None:
        try:
            reconcile()
        except Exception as exc:
            logger.warning("drift: initial reconcile failed: %s", exc)
        # _stop.wait returns True when set (shutdown), False on timeout (tick).
        while not _stop.wait(_DAILY_SECONDS):
            try:
                reconcile()
            except Exception as exc:
                logger.warning("drift: reconcile failed: %s", exc)

    threading.Thread(target=_loop, name="drift-detector", daemon=True).start()
