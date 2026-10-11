"""Provider that wraps the existing built-in tools.

This adapter does not move or rewrite tools. It exposes the canonical dispatch
table and either the full schema inventory or a run-scoped selected subset
through the ToolProvider protocol.
"""
from __future__ import annotations

import logging
import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

from app.application.code_agent.tools import (
    SandboxError,
    build_tool_dispatch,
    build_tool_schemas,
)
from app.application.agent_kernel.tool_result import ensure_tool_result
from app.application.agent_kernel.runtime_context import bind_runtime_context, current_runtime_context


logger = logging.getLogger(__name__)


class BuiltinToolProvider:
    """Canonical built-in dispatch with optional schema composition."""

    name = "builtin"

    def __init__(
        self,
        project_root: Path,
        tool_names: Collection[str] | None = None,
    ) -> None:
        self._project_root = project_root.resolve()
        # Cache one dispatch table per provider so lambdas are not rebuilt for
        # every call. Schema filtering changes visibility, not implementation.
        self._dispatch_table = build_tool_dispatch(self._project_root)
        selected = None if tool_names is None else {
            str(name).strip() for name in tool_names if str(name).strip()
        }
        schemas = build_tool_schemas()
        self._schemas = [
            schema for schema in schemas
            if selected is None
            or str((schema.get("function") or {}).get("name") or "") in selected
        ]
        # Visibility is prompt composition only. The canonical provider remains
        # able to dispatch every implemented built-in even when its schema was
        # not selected for this model turn.
        self._owned_names = set(self._dispatch_table)

    def is_enabled(self) -> bool:
        # The greeting fast path omits this provider entirely.
        return True

    def get_schemas(self) -> list[dict[str, Any]]:
        return self._schemas

    def owns(self, tool_name: str) -> bool:
        return tool_name in self._owned_names

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = self._dispatch_table.get(tool_name)
        if handler is None:
            return {"ok": False, "error": "unknown_tool",
                    "text": f"ERROR: unknown built-in tool '{tool_name}'"}
        try:
            # Provider-owned context never becomes a model-supplied argument.
            context = {
                **current_runtime_context(),
                "project_root": str(self._project_root),
                "visible_builtin_tools": json.dumps([
                    str((schema.get("function") or {}).get("name") or "")
                    for schema in self._schemas
                ]),
            }
            handler_args = dict(args)
            if tool_name in {"todo_update", "delegate_task", "resource_publish"}:
                handler_args["run_id"] = str(context.get("run_id") or "")
            if tool_name == "delegate_task":
                handler_args["permission_mode"] = str(context.get("permission_mode") or "ask")
            with bind_runtime_context(context):
                result = handler(**handler_args)
        except SandboxError as exc:
            return {"ok": False, "error": "sandbox_violation",
                    "text": f"ERROR: sandbox violation: {exc}"}
        except TypeError as exc:
            return {"ok": False, "error": "bad_arguments",
                    "text": f"ERROR: bad arguments to {tool_name}: {exc}"}
        except Exception as exc:
            logger.exception("Built-in tool %s crashed", tool_name)
            # A crash is a FAILURE: without ok=False the loop/journal would
            # record the ERROR text as a successful call (the real Mini CRM
            # App.css event did exactly that).
            return {"ok": False, "error": "tool_exception", "text": f"ERROR: {exc}"}
        return ensure_tool_result(result, source=f"built-in tool {tool_name!r}")
