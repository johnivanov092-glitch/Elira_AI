"""Server-owned metadata for the current tool dispatch worker."""
from __future__ import annotations

import contextvars


_EXECUTION_CHANNEL: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tool_execution_channel", default="local"
)


def set_execution_channel(channel: str) -> contextvars.Token:
    value = "remote" if channel == "remote" else "local"
    return _EXECUTION_CHANNEL.set(value)


def reset_execution_channel(token: contextvars.Token) -> None:
    _EXECUTION_CHANNEL.reset(token)


def get_execution_channel() -> str:
    return _EXECUTION_CHANNEL.get()
