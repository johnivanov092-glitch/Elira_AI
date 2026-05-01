"""PlannerV2 service — compatibility shim.

All logic lives in ``app.application.planner_v2.runtime``.
``PlannerV2Service`` is re-exported for ``agents_service.py`` and the
test suite (``test_temporal_internet_mode.py``, ``test_agent_os_phase3.py``).
"""
from __future__ import annotations

from app.application.planner_v2.runtime import PlannerV2Service

__all__ = ["PlannerV2Service"]
