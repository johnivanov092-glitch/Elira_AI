"""SSRF guard: `allow_private_address` opt-in for self-hosted LAN MCP servers.

The HTTP MCP client refuses private/loopback/metadata targets by default (SSRF
protection for untrusted remote MCP servers). A strictly-local user running a
self-hosted MCP on their own LAN (e.g. hass-mcp at 192.168.88.15) needs a
per-server opt-in — this pins that the opt-in relaxes ONLY the address block,
not the plain-http-scheme guard, and never relaxes anything by default.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_providers.mcp_http_client import (  # noqa: E402
    McpSecurityError,
    _guard_url,
)

LAN = "http://192.168.88.15:8124/mcp"


def test_private_ip_blocked_by_default() -> None:
    with pytest.raises(McpSecurityError):
        _guard_url(LAN, allow_insecure_http=True)


def test_private_ip_allowed_with_opt_in() -> None:
    # Should NOT raise — the LAN address is explicitly trusted.
    _guard_url(LAN, allow_insecure_http=True, allow_private_address=True)


def test_loopback_allowed_with_opt_in() -> None:
    _guard_url("http://127.0.0.1:8124/mcp", allow_insecure_http=True, allow_private_address=True)


def test_opt_in_does_not_relax_http_scheme_guard() -> None:
    # allow_private_address must NOT bypass the plain-http opt-in — the two
    # guards are independent.
    with pytest.raises(McpSecurityError):
        _guard_url(LAN, allow_insecure_http=False, allow_private_address=True)


def test_cloud_metadata_still_blocked_without_opt_in() -> None:
    with pytest.raises(McpSecurityError):
        _guard_url("http://169.254.169.254/latest/meta-data", allow_insecure_http=True)
