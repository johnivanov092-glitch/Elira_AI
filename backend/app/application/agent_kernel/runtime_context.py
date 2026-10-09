"""Trusted per-call metadata, separate from model supplied tool arguments."""
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator, Mapping

_CURRENT: ContextVar[Mapping[str, str]] = ContextVar("tool_runtime_context", default={})

def current_runtime_context() -> Mapping[str, str]:
    return _CURRENT.get()

@contextmanager
def bind_runtime_context(values: Mapping[str, str]) -> Iterator[None]:
    token = _CURRENT.set(dict(values))
    try:
        yield
    finally:
        _CURRENT.reset(token)
