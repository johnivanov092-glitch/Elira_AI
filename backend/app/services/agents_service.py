"""Compatibility alias for chat agent runtime entry points."""
from __future__ import annotations

import sys

from app.application.chat import runtime as _runtime

sys.modules[__name__] = _runtime
