"""Tool provider abstraction for the code-agent.

Why this exists: the agent's tool list comes from multiple sources —
the built-in filesystem/shell/web/sandbox tools, future SSH tools,
and external MCP servers. Each source has its own schema generation,
dispatch mechanics, and error semantics, but the LLM must see them
as one flat list.

The `ToolProvider` protocol is the common shape every source
satisfies; `ToolRegistry` aggregates schemas and routes dispatch
calls to the right provider. The agent loop talks to the registry
instead of any individual provider.

Public re-exports:
  ToolProvider       — Protocol every source implements
  ToolDispatchResult — return shape of registry.dispatch
  ToolRegistry       — aggregator + router
  BuiltinToolProvider — wraps app/application/code_agent/tools.py
"""

from app.application.tool_providers.base import ToolDispatchResult, ToolProvider
from app.application.tool_providers.builtin import BuiltinToolProvider
from app.application.tool_providers.registry import ToolRegistry

__all__ = [
    "BuiltinToolProvider",
    "ToolDispatchResult",
    "ToolProvider",
    "ToolRegistry",
]
