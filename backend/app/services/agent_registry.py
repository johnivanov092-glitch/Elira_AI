"""Compatibility alias for the Agent Registry application runtime."""
from __future__ import annotations

import sys

from app.application.agent_registry import runtime as _runtime

sys.modules[__name__] = _runtime
