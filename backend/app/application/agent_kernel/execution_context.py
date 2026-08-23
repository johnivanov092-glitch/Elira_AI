"""Server-owned metadata for the current tool dispatch worker."""
from __future__ import annotations

import contextvars


_EXECUTION_CHANNEL: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tool_execution_channel", default="local"
)
_PERMISSION_MODE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tool_permission_mode", default="ask"
)


def set_execution_channel(channel: str) -> contextvars.Token:
    value = "remote" if channel == "remote" else "local"
    return _EXECUTION_CHANNEL.set(value)


def reset_execution_channel(token: contextvars.Token) -> None:
    _EXECUTION_CHANNEL.reset(token)


def get_execution_channel() -> str:
    return _EXECUTION_CHANNEL.get()


def set_permission_mode(permission_mode: str) -> contextvars.Token:
    value = str(permission_mode or "ask").strip().lower()
    if value not in {"ask", "accept_edits", "bypass"}:
        value = "ask"
    return _PERMISSION_MODE.set(value)


def reset_permission_mode(token: contextvars.Token) -> None:
    _PERMISSION_MODE.reset(token)


def get_permission_mode() -> str:
    return _PERMISSION_MODE.get()
