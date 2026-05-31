"""Compatibility alias for the tool registry application runtime."""
from __future__ import annotations

import sys

from app.application.tool_registry import runtime as _runtime

sys.modules[__name__] = _runtime
