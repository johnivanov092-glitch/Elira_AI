"""Compatibility alias for the event bus application runtime."""
from __future__ import annotations

import sys

from app.application.event_bus import runtime as _runtime

sys.modules[__name__] = _runtime
