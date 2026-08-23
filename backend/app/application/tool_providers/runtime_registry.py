"""Canonical provider registry shared by agent and Workflow tool steps."""
from __future__ import annotations

from pathlib import Path

from app.application.tool_providers.builtin import BuiltinToolProvider
from app.application.tool_providers.itops_provider import ItopsToolProvider
from app.application.tool_providers.lsp_provider import build_lsp_providers
from app.application.tool_providers.mcp_provider import build_mcp_providers
from app.application.tool_providers.registry import ToolRegistry
from app.application.tool_providers.ssh_provider import SshToolProvider


def build_runtime_tool_registry(project_root: Path | str) -> ToolRegistry:
    """Build the one live registry over the existing provider implementations."""
    root = Path(project_root).expanduser().resolve()
    return ToolRegistry([
        BuiltinToolProvider(root),
        SshToolProvider(),
        ItopsToolProvider(),
        *build_lsp_providers(),
        *build_mcp_providers(),
    ])
