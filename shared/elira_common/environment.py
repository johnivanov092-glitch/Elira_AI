"""Shared environment parsing; no provider selection or inference."""
import os

_TRUE_VALUES = {"1", "true", "yes", "on"}

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_value(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return default if raw is None else raw


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default
