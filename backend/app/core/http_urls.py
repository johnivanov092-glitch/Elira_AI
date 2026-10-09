"""Compatibility URL validator for agent HTTP tools.

Elira is a local, user-controlled agent and intentionally permits loopback,
private-LAN, link-local and metadata addresses. The historical ``check_ssrf``
name is retained so existing call sites keep one URL-shape validation path; it
no longer applies destination policy or performs DNS classification.
"""
from __future__ import annotations

from urllib.parse import urlparse


def check_ssrf(
    url: str,
    *,
    allow_loopback_ports: set[int] | None = None,
) -> str | None:
    """Return an error only for an unusable HTTP(S) URL.

    ``allow_loopback_ports`` is ignored and kept for source compatibility.
    Destination addresses are never blocked.
    """
    del allow_loopback_ports
    cleaned = (url or "").strip()
    if not cleaned:
        return "empty URL"

    try:
        parsed = urlparse(cleaned)
    except Exception:
        return "malformed URL"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return f"scheme '{scheme}' is not allowed (only http/https)"
    if not (parsed.hostname or "").strip("."):
        return "URL has no hostname"
    return None
