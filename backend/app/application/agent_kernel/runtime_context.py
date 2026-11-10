"""Trusted per-call metadata, separate from model supplied tool arguments."""
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator, Mapping

_CURRENT: ContextVar[Mapping[str, str]] = ContextVar("tool_runtime_context", default={})

# Tools that do the work itself: run a command or change a file.
WORK_TOOLS = frozenset({"run_bash", "run_server", "write_file", "edit_file"})
# Set to "1" for web tools once a work tool has run in the same run: the web is
# then a reference for the task, and each documentation lookup has its own budget.
WEB_REFERENCE_KEY = "web_reference"

def current_runtime_context() -> Mapping[str, str]:
    return _CURRENT.get()

@contextmanager
def bind_runtime_context(values: Mapping[str, str]) -> Iterator[None]:
    token = _CURRENT.set(dict(values))
    try:
        yield
    finally:
        _CURRENT.reset(token)
