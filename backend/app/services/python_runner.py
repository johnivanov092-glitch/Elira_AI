"""Python-runner service — compatibility shim.

All logic lives in ``app.application.python_runner.runtime``.
Public API re-exported for all callers: api/routes/tools_exec,
application/chat/post_processing, application/tool_registry/builtins.
"""
from __future__ import annotations

from app.application.python_runner.runtime import (
    ALLOWED_IMPORTS,
    SAFE_BUILTINS,
    execute_python,
)

__all__ = ["ALLOWED_IMPORTS", "SAFE_BUILTINS", "execute_python"]
