"""Aggregator + router over a list of ToolProvider instances.

One entry point for the agent loop: it asks the registry for the
flat list of schemas to expose to the model, and the registry
routes each dispatch call to the right provider by tool name.

Design notes:

  * First-registered-wins on name collisions, with a logged
    warning. This lets users override an MCP server's `web_search`
    by listing `BuiltinToolProvider` first.
  * Disabled providers contribute zero schemas and never receive
    dispatch calls — they're invisible to the model until enabled.
  * `dispatch()` never raises. JSON-parsing a malformed args blob
    yields {} (and the underlying tool then reports a missing-arg
    error in its standard way). Unknown tool names → an error
    tool_meta. This matches the historical _execute_tool_call
    semantics exactly so the agent_loop behaves identically.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from app.application.tool_providers.base import ToolDispatchResult, ToolProvider


logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self, providers: Iterable[ToolProvider]) -> None:
        self._providers: list[ToolProvider] = list(providers)
        # tool_name → provider, computed once per registry build
        self._owner: dict[str, ToolProvider] = {}
        self._schemas: list[dict[str, Any]] = []
        self._rebuild()

    def _rebuild(self) -> None:
        owner: dict[str, ToolProvider] = {}
        schemas: list[dict[str, Any]] = []
        for provider in self._providers:
            if not provider.is_enabled():
                continue
            for schema in provider.get_schemas():
                try:
                    name = schema["function"]["name"]
                except (KeyError, TypeError):
                    logger.warning(
                        "ToolRegistry: provider %r returned a malformed schema (no function.name) — skipping",
                        getattr(provider, "name", repr(provider)),
                    )
                    continue
                if name in owner:
                    incumbent = getattr(owner[name], "name", "?")
                    new = getattr(provider, "name", "?")
                    logger.warning(
                        "ToolRegistry: tool %r is offered by both %r (kept) and %r (dropped) — first-wins.",
                        name, incumbent, new,
                    )
                    continue
                owner[name] = provider
                schemas.append(schema)
        self._owner = owner
        self._schemas = schemas

    # ── Public API ──────────────────────────────────────────────

    def collect_schemas(self) -> list[dict[str, Any]]:
        """Flat list of tool schemas ready to pass to ollama.chat(tools=...)."""
        return list(self._schemas)

    def known_tools(self) -> set[str]:
        """Set of tool names currently exposed by enabled providers.

        Used by the agent's inline-tool-call fallback parser to drop
        hallucinated names that don't actually exist anywhere."""
        return set(self._owner.keys())

    def dispatch(self, tool_name: str, raw_args: Any) -> ToolDispatchResult:
        """Execute one tool call. Returns (tool_meta, parsed_args).

        `raw_args` may be a dict (typical native tool_calls path) or
        a JSON-encoded string (some Ollama models on inline-JSON
        path). Either way, we hand the underlying provider a dict.
        Providers never raise — failures come back as
        tool_meta["text"].
        """
        args = self._coerce_args(raw_args)
        provider = self._owner.get(tool_name)
        if provider is None:
            return ToolDispatchResult(
                tool_meta={"text": f"ERROR: unknown tool '{tool_name}'"},
                parsed_args=args,
            )
        try:
            tool_meta = provider.dispatch(tool_name, args)
        except Exception as exc:
            # Defense-in-depth: providers SHOULD swallow their own
            # exceptions, but if one slips through we don't want it
            # to kill the agent's whole streaming generator.
            logger.exception(
                "ToolRegistry: provider %r leaked an exception from dispatch(%r)",
                getattr(provider, "name", "?"), tool_name,
            )
            tool_meta = {"text": f"ERROR: {exc}"}
        if not isinstance(tool_meta, dict):
            tool_meta = {"text": str(tool_meta)}
        return ToolDispatchResult(tool_meta=tool_meta, parsed_args=args)

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _coerce_args(raw_args: Any) -> dict[str, Any]:
        """Normalize whatever the LLM emitted to a flat dict.

        Native Ollama tool_calls deliver `args` as a dict already.
        Some inline-JSON paths deliver it as a JSON string. Bad JSON
        or unsupported shapes yield {}.
        """
        if isinstance(raw_args, dict):
            return dict(raw_args)
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}
