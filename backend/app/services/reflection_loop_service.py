"""Reflection-loop service — compatibility shim.

All logic lives in ``app.application.reflection_loop.runtime``.
Public API re-exported for callers that import run_reflection_loop directly.
"""
from __future__ import annotations

from app.application.reflection_loop.runtime import run_reflection_loop

__all__ = ["run_reflection_loop"]
