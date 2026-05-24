"""Compatibility alias for the legacy elira_phase19 runtime module."""
from __future__ import annotations

import sys

from app.application.elira_multi_file_loop import runtime as _runtime

sys.modules[__name__] = _runtime
