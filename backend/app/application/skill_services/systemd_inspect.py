"""Application adapter to the mutable linux-admin skill; no domain implementation."""
import sys
from typing import Any
from app.core.skill_modules import load_skill_module

_implementation = load_skill_module('linux-admin', 'systemd_inspect.py')


def __getattr__(name: str) -> Any:
    """Expose the installed module dynamically; its API is validated by integration tests."""
    return getattr(_implementation, name)


sys.modules[__name__] = _implementation
