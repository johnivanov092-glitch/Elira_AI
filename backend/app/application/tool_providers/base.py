"""Common shape for any source of agent tools.

Built-in tools, SSH tools, and MCP-server tools all satisfy this
Protocol. The registry doesn't care which kind of provider it's
talking to — it just collects schemas and routes dispatch.
"""
from __future__ import annotations

from typing import Any, NamedTuple, Protocol, runtime_checkable


class ToolDispatchResult(NamedTuple):
    """What `ToolRegistry.dispatch` returns for one tool call.

    `tool_meta` is the structured result the provider produced
    (always has a `text` key with the LLM-readable summary; may
    have extra keys like `touched_path` / `old_content` /
    `new_content` / `diff_action` that the SSE stream forwards).

    `parsed_args` is whatever the registry resolved the raw_args
    blob into (a dict, possibly empty). Lifted out so the agent
    loop can log it in the tool_call SSE event without re-parsing.
    """

    tool_meta: dict[str, Any]
    parsed_args: dict[str, Any]


@runtime_checkable
class ToolProvider(Protocol):
    """One source of tools the agent can call.

    Implementations must NEVER raise from `dispatch`. All known
    failure modes (sandbox violations, bad arguments, network
    issues, server crashes) are reported by returning a tool_meta
    dict with `{"text": "ERROR: ..."}`. Letting an exception
    propagate would crash the streaming generator that wraps the
    whole agent loop.
    """

    name: str

    def is_enabled(self) -> bool:
        """Provider can be turned off without removing it from the
        registry (e.g. SSH disabled because no allowlist configured,
        or MCP server not yet started). Disabled providers contribute
        no schemas and are never asked to dispatch."""
        ...

    def get_schemas(self) -> list[dict[str, Any]]:
        """Ollama-style tool schemas:
            [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]

        Tool names must be unique within the provider; the registry
        enforces uniqueness across providers (first-registered wins
        on collisions, with a logged warning)."""
        ...

    def owns(self, tool_name: str) -> bool:
        """Return True if this provider should handle `tool_name`.
        Default implementation checks against get_schemas() — but
        providers with cheaper lookup (e.g. dict-backed) can override."""
        ...

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute one tool call. `args` is already a dict (the
        registry handles JSON-string → dict conversion). Always
        returns a dict with at least a `text` field; never raises."""
        ...
