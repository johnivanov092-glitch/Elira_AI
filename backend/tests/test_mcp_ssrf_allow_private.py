"""HTTP MCP accepts every HTTP(S) destination without a host allowlist."""
from __future__ import annotations

import pytest

from app.application.tool_providers.mcp_http_client import McpSecurityError, _guard_url


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.88.15:8124/mcp",
        "http://127.0.0.1:8124/mcp",
        "http://169.254.169.254/latest/meta-data",
        "https://example.com/mcp",
    ],
)
def test_http_destinations_are_allowed_without_opt_in(url: str) -> None:
    _guard_url(url, allow_insecure_http=False, allow_private_address=False)


@pytest.mark.parametrize("url", ["ftp://example.com/", "file:///tmp/x", "https:///missing-host"])
def test_invalid_transport_shape_is_rejected(url: str) -> None:
    with pytest.raises(McpSecurityError):
        _guard_url(url, allow_insecure_http=False)
