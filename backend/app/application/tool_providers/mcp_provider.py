"""Adapter that exposes one running MCP server's tools through the
ToolProvider Protocol.

One McpToolProvider instance per server. Tool names are namespaced
as `<server_id>__<original_name>` so two servers can both expose
`search` without colliding inside the agent's tool list.

Conversion rules:

  * MCP inputSchema -> function.parameters: pass through
    unchanged (both use JSON-Schema dialect, both have type/
    properties/required at the top level).
  * MCP tools/call result → tool_meta: MCP returns
        {"content": [{"type": "text", "text": "..."}, ...],
         "isError": bool}
    We flatten the content array into one `text` blob the LLM can
    consume, and tag with `mcp_server` for the SSE event.

What this provider exposes to the ToolRegistry:
  * MCP tools only. Resources/prompts are available through McpClient and API
    context routes, not as executable tools.
What we do NOT support yet:
  * Streaming progress notifications during a tool call
  * Server-initiated requests (sampling, roots)
"""
from __future__ import annotations

import logging
from typing import Any

from app.application.tool_providers.mcp_client import McpError
from app.application.tool_providers.mcp_runtime import get_live_client, list_servers


logger = logging.getLogger(__name__)


# Tool-name prefix delimiter. Two underscores: rare enough in real
# tool names to be a safe sentinel, valid in JSON identifier names
# so most LLM tokenizers handle it well.
_NAMESPACE_DELIM = "__"


def _qualify(server_id: str, tool_name: str) -> str:
    return f"{server_id}{_NAMESPACE_DELIM}{tool_name}"


def _unqualify(qualified: str, server_id: str) -> str:
    prefix = f"{server_id}{_NAMESPACE_DELIM}"
    if qualified.startswith(prefix):
        return qualified[len(prefix):]
    return qualified


def _flatten_mcp_result(result: dict[str, Any]) -> dict[str, Any]:
    """Turn `tools/call`'s result envelope into `{text, ...}` shape."""
    is_error = bool(result.get("isError"))
    parts: list[str] = []
    for chunk in result.get("content", []) or []:
        if not isinstance(chunk, dict):
            continue
        kind = chunk.get("type")
        if kind == "text":
            text_val = chunk.get("text")
            if isinstance(text_val, str):
                parts.append(text_val)
        elif kind in ("image", "resource"):
            # We don't render images/binary resources back to the
            # agent yet — just leave a marker. Future work: extract
            # text-bearing fields, store binary as touched_path.
            parts.append(f"[{kind} omitted]")
    body = "\n".join(parts) if parts else ""
    if is_error:
        return {"text": f"ERROR (mcp): {body or 'unknown error'}"}
    return {"text": body}


class McpToolProvider:
    """One MCP server, presented as a ToolProvider.

    Cached state:
      * The qualified-name → original-name map (built once on
        construction by calling tools/list).
      * The pre-converted function schema list.

    If the server isn't currently running, `is_enabled()` returns
    False and the provider contributes nothing to the registry —
    the registry skips it just like a disabled built-in provider.
    """

    def __init__(self, server_id: str) -> None:
        self.name = f"mcp:{server_id}"
        self._server_id = server_id
        self._schemas: list[dict[str, Any]] = []
        self._owned: set[str] = set()
        self._qualified_to_original: dict[str, str] = {}
        self._populated = False

    # ── ToolProvider ────────────────────────────────────────────

    def is_enabled(self) -> bool:
        return get_live_client(self._server_id) is not None

    def get_schemas(self) -> list[dict[str, Any]]:
        # Populate lazily so we can recover after a restart.
        if not self._populated:
            self._refresh_schemas()
        return self._schemas

    def owns(self, tool_name: str) -> bool:
        if not self._populated:
            self._refresh_schemas()
        return tool_name in self._owned

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        client = get_live_client(self._server_id)
        if client is None:
            return {"text": f"ERROR: mcp server '{self._server_id}' is not running"}
        if tool_name not in self._qualified_to_original:
            # Schemas may have been built when a different set of
            # tools was advertised; try once to refresh.
            self._refresh_schemas()
        original_name = self._qualified_to_original.get(tool_name)
        if original_name is None:
            return {"text": f"ERROR: tool '{tool_name}' not found on server '{self._server_id}'"}
        try:
            raw_result = client.call_tool(original_name, args)
        except McpError as exc:
            return {"text": f"ERROR: {exc}"}
        except Exception as exc:
            logger.exception("mcp dispatch %s failed", tool_name)
            return {"text": f"ERROR: {exc}"}
        meta = _flatten_mcp_result(raw_result)
        meta["mcp_server"] = self._server_id
        return meta

    # ── Internals ───────────────────────────────────────────────

    def _refresh_schemas(self) -> None:
        client = get_live_client(self._server_id)
        if client is None:
            self._schemas = []
            self._owned = set()
            self._qualified_to_original = {}
            self._populated = True
            return
        try:
            tools = client.list_tools()
        except McpError as exc:
            logger.warning("mcp tools/list on %r failed: %s", self._server_id, exc)
            self._schemas = []
            self._owned = set()
            self._qualified_to_original = {}
            self._populated = True
            return

        schemas: list[dict[str, Any]] = []
        mapping: dict[str, str] = {}
        for tool in tools:
            original_name = tool.get("name")
            if not isinstance(original_name, str) or not original_name:
                continue
            qualified = _qualify(self._server_id, original_name)
            description = tool.get("description") or ""
            input_schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
            schemas.append({
                "type": "function",
                "function": {
                    "name": qualified,
                    "description": (
                        f"[mcp:{self._server_id}] {description}".strip()
                        if description
                        else f"[mcp:{self._server_id}] (no description)"
                    ),
                    "parameters": input_schema if isinstance(input_schema, dict) else {"type": "object"},
                },
            })
            mapping[qualified] = original_name

        self._schemas = schemas
        self._owned = set(mapping.keys())
        self._qualified_to_original = mapping
        self._populated = True


def _mcp_noop_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Placeholder handler for an MCP ToolSpec row.

    MCP dispatch goes through McpToolProvider, never through the registry handler;
    this exists only so the registry has a callable bound to the spec.
    """
    return {"ok": False, "text": "ERROR: MCP tool — dispatch via MCP provider, not tool_registry"}


def sync_mcp_tool_specs(providers: list["McpToolProvider"]) -> None:
    """Mirror running MCP servers' tools into the Tool Registry, fail-closed.

    Each MCP tool gets a DB ToolSpec that is forbidden + disabled +
    policy_classified=0 on first sight (untrusted remote code) — it cannot run until
    an admin classifies it via the Tool API. Re-sync preserves admin policy
    (register_dynamic_tool refreshes metadata only). MCP-source specs whose tool is
    no longer advertised by any running server are DISABLED (stale → off, never
    deleted, so classification/audit survives a transient outage).

    Defensive throughout: a registry or network hiccup must never break provider
    construction or the agent loop.
    """
    try:
        import app.application.tool_registry.runtime as _tr
    except Exception:
        return

    live: set[str] = set()
    for provider in providers:
        try:
            schemas = provider.get_schemas()
        except Exception:
            continue
        for schema in schemas:
            fn = schema.get("function") if isinstance(schema, dict) else None
            if not isinstance(fn, dict):
                continue
            qname = fn.get("name")
            if not isinstance(qname, str) or not qname:
                continue
            params = fn.get("parameters")
            live.add(qname)
            try:
                _tr.register_dynamic_tool(
                    qname,
                    _mcp_noop_handler,
                    display_name=qname,
                    description=str(fn.get("description") or ""),
                    category="mcp",
                    parameters_schema=params if isinstance(params, dict) else {},
                    source="mcp",
                    side_effect=True,
                    scopes=[],
                    timeout_seconds=60,
                )
            except Exception as exc:
                logger.warning("mcp spec sync for %r failed: %s", qname, exc)

    try:
        for tool in _tr.list_tools_with_schemas(source="mcp", enabled_only=False):
            tname = tool.get("name")
            if tname and tname not in live and tool.get("enabled"):
                _tr.update_tool(tname, {"enabled": False})
    except Exception as exc:
        logger.warning("mcp stale-spec disable sweep failed: %s", exc)


def build_mcp_providers() -> list[McpToolProvider]:
    """Construct a provider per **running** MCP server.

    Called once at agent_loop start. Servers that aren't running
    are skipped here so the registry doesn't get providers that
    contribute zero schemas — that's just noise."""
    providers: list[McpToolProvider] = []
    for spec in list_servers():
        if spec.get("status") != "running":
            continue
        providers.append(McpToolProvider(spec["id"]))
    # P9.2-FIXUP: mirror discovered MCP tools into the registry fail-closed so the
    # unified kernel can enforce policy on them (forbidden/disabled/unclassified by
    # default; admin classification required to run).
    sync_mcp_tool_specs(providers)
    return providers
