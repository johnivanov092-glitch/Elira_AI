"""IT-Ops startup sequence — flag-gated, NOT model-callable.

Invoked from app startup and initializes the portable IT Ops metadata store.

There is NO startup auto-recovery and NO automatic secret deletion. An incomplete
portable-vault provisioning record stays visible for explicit workflow recovery.
The portable data key is never unlocked at startup and never depends on Windows.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def itops_startup() -> None:
    """Initialize metadata only. Never unlock, recover, or delete secrets."""
    from app.infrastructure.it_ops import store

    store.init_db()   # migrate/verify only — NO auto-recovery
    logger.info("itops store initialized")
