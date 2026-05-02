"""Runtime service — compatibility shim.

All logic lives in ``app.application.runtime_status.runtime``.
Public API re-exported for all callers: api/routes/runtime, app/main,
and tests.
"""
from __future__ import annotations

from app.application.runtime_status.runtime import get_runtime_status, init_runtime_state

__all__ = ["get_runtime_status", "init_runtime_state"]
