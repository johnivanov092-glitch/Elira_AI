"""Compatibility alias for the monitoring application runtime."""
from __future__ import annotations

import sys

from app.application.monitoring import runtime as _runtime

sys.modules[__name__] = _runtime
