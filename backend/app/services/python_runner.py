"""Thin facade — all Python runner logic lives in infrastructure/runtime/python_runner.py."""
from app.infrastructure.runtime.python_runner import (  # noqa: F401
    ALLOWED_IMPORTS,
    SAFE_BUILTINS,
    _safe_import,
    execute_python,
)
