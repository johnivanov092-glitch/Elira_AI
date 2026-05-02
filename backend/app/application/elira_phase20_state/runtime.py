"""Compatibility alias for the legacy elira_phase20_state runtime module."""
from __future__ import annotations

import sys

from app.application.elira_execution_state import runtime as _runtime

sys.modules[__name__] = _runtime
