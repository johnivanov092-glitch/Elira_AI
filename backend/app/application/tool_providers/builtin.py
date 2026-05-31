"""Provider that wraps the existing built-in tools.

This is the minimum-change adapter: it doesn't move or rewrite any
tool — just exposes `build_tool_schemas` and `build_tool_dispatch`
from `app.application.code_agent.tools` through the ToolProvider
protocol. All 78 existing code-agent tests pass unchanged.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.application.code_agent.tools import (
    SandboxError,
    build_tool_dispatch,
    build_tool_schemas,
)


logger = logging.getLogger(__name__)


class BuiltinToolProvider:
    """Read/write/edit/glob/grep/run_bash/recall/web_search/web_fetch
    /sandbox_run/sandbox_reset — the original toolbox."""

    name = "builtin"

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve()
        # The original tools.py returns a fresh dict on every call;
        # cache one instance per provider so we don't rebuild the
        # lambdas for every dispatch.
        self._dispatch_table = build_tool_dispatch(self._project_root)
        self._schemas = build_tool_schemas()
        self._owned_names = {s["function"]["name"] for s in self._schemas}

    def is_enabled(self) -> bool:
        # Built-in tools are always on; there's no scenario where you
        # want the code-agent without them.
        return True

    def get_schemas(self) -> list[dict[str, Any]]:
        return self._schemas

    def owns(self, tool_name: str) -> bool:
        return tool_name in self._owned_names

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = self._dispatch_table.get(tool_name)
        if handler is None:
            return {"text": f"ERROR: unknown built-in tool '{tool_name}'"}
        try:
            result = handler(**args)
        except SandboxError as exc:
            return {"text": f"ERROR: sandbox violation: {exc}"}
        except TypeError as exc:
            return {"text": f"ERROR: bad arguments to {tool_name}: {exc}"}
        except Exception as exc:
            logger.exception("Built-in tool %s crashed", tool_name)
            return {"text": f"ERROR: {exc}"}
        # tools.py returns either a dict (with `text` + optional extras)
        # or, rarely, a non-dict — normalize to keep the registry's
        # caller invariants stable.
        if isinstance(result, dict):
            return result
        return {"text": str(result)}
