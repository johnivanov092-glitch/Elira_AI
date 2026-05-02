"""Compatibility alias for the workflow engine application runtime."""
from __future__ import annotations

import sys

from app.application.workflow_engine import runtime as _runtime

sys.modules[__name__] = _runtime
