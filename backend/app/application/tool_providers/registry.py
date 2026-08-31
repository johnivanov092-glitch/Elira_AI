"""Aggregator + router over a list of ToolProvider instances.

One entry point for the agent loop: it asks the registry for the
flat list of schemas to expose to the model, and the registry
routes each dispatch call to the right provider by tool name.

Design notes:

  * First-registered-wins on name collisions, with a logged
    warning. This lets users override an MCP server's `web_search`
    by listing `BuiltinToolProvider` first.
  * Providers report schemas for their live runtime state. Lifecycle operations
    such as starting MCP stay explicit tools, not authorization gates.
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
from app.application.agent_kernel.tool_result import ensure_tool_result


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
        """Flat list of OpenAI-compatible tool schemas."""
        return list(self._schemas)

    def known_tools(self) -> set[str]:
        """Set of tool names currently exposed by enabled providers.

        Used by the agent's inline-tool-call fallback parser to drop
        hallucinated names that don't actually exist anywhere."""
        return set(self._owner.keys())

    def dispatch(self, tool_name: str, raw_args: Any) -> ToolDispatchResult:
        """Execute one tool call. Returns (tool_meta, parsed_args).

        `raw_args` may be a dict (typical native tool_calls path) or
        a JSON-encoded string (some local models on inline-JSON
        path). Either way, we hand the underlying provider a dict.
        Providers never raise — failures come back as
        tool_meta["text"].
        """
        args = self._coerce_args(raw_args)
        provider = self._resolve_provider(tool_name)
        if provider is None:
            return ToolDispatchResult(
                tool_meta={
                    "ok": False,
                    "error": "unknown_tool",
                    "text": f"ERROR: unknown tool '{tool_name}'",
                },
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
            tool_meta = {"ok": False, "error": "provider_exception", "text": f"ERROR: {exc}"}
        tool_meta = ensure_tool_result(
            tool_meta,
            source=f"provider result for {tool_name!r}",
        )
        return ToolDispatchResult(tool_meta=tool_meta, parsed_args=args)

    def dispatch_raw(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Dispatch pre-coerced args. Returns tool_meta dict (no ToolDispatchResult).

        Used by the unified ToolExecutor so it can own arg coercion and audit
        without re-parsing args a second time inside dispatch().
        """
        provider = self._resolve_provider(tool_name)
        if provider is None:
            return {"ok": False, "error": "unknown_tool", "text": f"ERROR: unknown tool '{tool_name}'"}
        try:
            result = provider.dispatch(tool_name, args)
        except Exception as exc:
            logger.exception(
                "ToolRegistry.dispatch_raw: provider %r leaked exception for %r",
                getattr(provider, "name", "?"), tool_name,
            )
            result = {"ok": False, "error": "provider_exception", "text": f"ERROR: {exc}"}
        return ensure_tool_result(
            result,
            source=f"provider result for {tool_name!r}",
        )

    # ── Helpers ─────────────────────────────────────────────────

    def _resolve_provider(self, tool_name: str) -> ToolProvider | None:
        """Find an execution owner independently from prompt visibility."""
        visible_owner = self._owner.get(tool_name)
        if visible_owner is not None:
            return visible_owner
        for provider in self._providers:
            try:
                if provider.is_enabled() and provider.owns(tool_name):
                    return provider
            except Exception:
                logger.warning(
                    "ToolRegistry: provider %r failed owns(%r)",
                    getattr(provider, "name", repr(provider)),
                    tool_name,
                    exc_info=True,
                )
        return None

    @staticmethod
    def _coerce_args(raw_args: Any) -> dict[str, Any]:
        """Normalize whatever the LLM emitted to a flat dict.

        Native tool_calls deliver `args` as a dict already.
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
