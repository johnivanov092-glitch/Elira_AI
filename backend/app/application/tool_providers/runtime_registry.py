"""Canonical provider registry shared by agent and Workflow tool steps."""
from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path

from app.application.tool_providers.base import ToolProvider
from app.application.tool_providers.builtin import BuiltinToolProvider
from app.application.tool_providers.itops_provider import ItopsToolProvider
from app.application.tool_providers.lsp_provider import build_lsp_providers
from app.application.tool_providers.mcp_provider import build_mcp_providers
from app.application.tool_providers.registry import ToolRegistry
from app.application.tool_providers.ssh_provider import SshToolProvider


def build_runtime_tool_registry(
    project_root: Path | str,
    *,
    include_builtin: bool = True,
    builtin_tool_names: Collection[str] | None = None,
    mcp_server_ids: Collection[str] | None = None,
    mcp_schema_queries: Mapping[str, str] | None = None,
    lsp_server_ids: Collection[str] | None = None,
    include_ssh: bool = True,
    include_itops: bool = True,
) -> ToolRegistry:
    """Build the one registry over existing providers.

    ``builtin_tool_names=None`` keeps the full inventory/API view for
    compatibility. The agent passes explicit per-run built-in groups and
    integration sets, so optional schemas contribute no prompt tokens until
    requested by the model.
    """
    root = Path(project_root).expanduser().resolve()
    providers: list[ToolProvider] = []
    if include_builtin:
        providers.append(BuiltinToolProvider(root, builtin_tool_names))
    if include_ssh:
        providers.append(SshToolProvider())
    if include_itops:
        providers.append(ItopsToolProvider())
    providers.extend(build_lsp_providers(lsp_server_ids))
    providers.extend(build_mcp_providers(
        mcp_server_ids,
        schema_queries=dict(mcp_schema_queries or {}),
    ))
    return ToolRegistry(providers)
