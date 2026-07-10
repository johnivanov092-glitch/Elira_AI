"""IT-Ops startup sequence — flag-gated, NOT model-callable.

Invoked only from app startup (app.main). When the `itops` flag is OFF this is a
no-op: the schema, vault, and recovery are never touched. When ON it runs the
correct order — migrate the store, then recover incomplete secrets — BEFORE the
app begins accepting secret intake, and logs the recovery outcome using opaque
secret_refs only (never a secret value).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def itops_startup() -> None:
    """Migrate the it_ops store, then run the secret recovery pass. Flag-gated and
    fail-safe. Returns None; raises nothing the caller must handle (the caller wraps
    it defensively too)."""
    from app.application.feature_flags import flag_enabled

    if not flag_enabled("itops"):
        return  # OFF: do not touch schema / vault / recovery

    from app.infrastructure.it_ops import store
    from app.infrastructure.secrets import vault

    store.init_db()                       # migrate/verify BEFORE any intake
    summary = vault.recover_incomplete_secrets()
    cleaned = summary["cleaned"]
    failed = summary["failed"]
    skipped = summary["skipped"]
    exhausted = summary.get("exhausted", [])
    # ALWAYS log a bounded summary (even all-zero), opaque secret_refs only — a
    # value must never reach the log.
    logger.info(
        "itops startup recovery: cleaned=%d failed=%d skipped=%d exhausted=%d refs=%s",
        len(cleaned), len(failed), len(skipped), len(exhausted),
        {"cleaned": cleaned,
         "failed": [f["secret_ref"] for f in failed],
         "skipped": skipped,
         "exhausted": exhausted})
