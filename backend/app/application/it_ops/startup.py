"""IT-Ops startup sequence — flag-gated, NOT model-callable.

Invoked only from app startup (app.main). When the `itops` flag is OFF this is a
no-op: the schema is never touched. When ON it ONLY initializes the store.

Phase-0 decision: there is NO startup auto-recovery and NO automatic secret
deletion. An incomplete provisioning record stays visible for MANUAL recovery.
This removes the whole concurrent claim/delete race class (the recovery machinery
in the vault/store is left inert — nothing calls it).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def itops_startup() -> None:
    """Initialize the it_ops store. Flag-gated and fail-safe. No recovery, no
    deletion — the caller wraps this defensively too."""
    from app.application.feature_flags import flag_enabled

    if not flag_enabled("itops"):
        return  # OFF: do not touch the schema

    from app.infrastructure.it_ops import store

    store.init_db()   # migrate/verify only — NO auto-recovery
    logger.info("itops store initialized")
