"""Compatibility facade for restricted Python execution."""
from __future__ import annotations

from app.domain.runtime.python_runner import ALLOWED_IMPORTS, SAFE_BUILTINS, execute_python

__all__ = ["ALLOWED_IMPORTS", "SAFE_BUILTINS", "execute_python"]
