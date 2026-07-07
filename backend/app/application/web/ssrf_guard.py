"""SSRF guard — block requests to private, loopback, and link-local addresses.

By default Elira only allows outbound HTTP/HTTPS to routable public addresses.
Requests that resolve to private ranges are blocked to prevent Server-Side
Request Forgery attacks where the agent is tricked into probing internal
infrastructure.

Blocked ranges (RFC 1918, RFC 5735, RFC 4291 §2.5.3, RFC 3927):
  - 127.0.0.0/8      loopback
  - 10.0.0.0/8       private
  - 172.16.0.0/12    private
  - 192.168.0.0/16   private
  - 169.254.0.0/16   link-local (APIPA)
  - 0.0.0.0/8        "this" network
  - ::1              IPv6 loopback
  - fc00::/7         IPv6 unique-local
  - fe80::/10        IPv6 link-local

Hostname aliases: ``localhost`` is also blocked regardless of resolution.

Usage::

    from app.application.web.ssrf_guard import check_ssrf

    reason = check_ssrf("http://192.168.1.1/admin")
    if reason:
        return {"text": f"ERROR: SSRF blocked — {reason}"}
    # safe to proceed
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Private/reserved IP networks that must never be contacted.
_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("10.0.0.0/8"),          # private class A
    ipaddress.ip_network("172.16.0.0/12"),       # private class B
    ipaddress.ip_network("192.168.0.0/16"),      # private class C
    ipaddress.ip_network("169.254.0.0/16"),      # link-local APIPA
    ipaddress.ip_network("0.0.0.0/8"),           # "this" network
    ipaddress.ip_network("::1/128"),             # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),            # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),           # IPv6 link-local
    ipaddress.ip_network("100.64.0.0/10"),       # CGNAT shared address space
]

# Hostnames that are always blocked regardless of DNS resolution.
_BLOCKED_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "localhost.localdomain",
    "local",
    "broadcasthost",
    "ip6-localhost",
    "ip6-loopback",
    "ip6-localnet",
    "metadata.google.internal",   # GCP metadata server
    "169.254.169.254",             # EC2/Azure/GCP metadata address
})

_DNS_TIMEOUT: float = 2.0  # seconds

# Loopback host aliases (a subset of _BLOCKED_HOSTNAMES). Only these may be opened
# up by `allow_loopback_ports` — the cloud-metadata / broadcast aliases never are.
_LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
})


def _ip_is_blocked(addr: str) -> str | None:
    """Return a reason string if *addr* (IP string) is in a blocked network."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return None
    if ip.is_loopback:
        return f"loopback address ({addr})"
    if ip.is_link_local:
        return f"link-local address ({addr})"
    for network in _BLOCKED_NETWORKS:
        if ip in network:
            return f"private/reserved address ({addr} in {network})"
    return None


def _resolve_host(hostname: str) -> list[str]:
    """Resolve hostname to a list of IP address strings. Returns [] on failure."""
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(_DNS_TIMEOUT)
        results = socket.getaddrinfo(hostname, None)
        return list({r[4][0] for r in results})
    except Exception:
        return []
    finally:
        socket.setdefaulttimeout(old)


def check_ssrf(url: str, *, allow_loopback_ports: set[int] | None = None) -> str | None:
    """Return a blocking reason string if *url* should not be fetched, else None.

    Call this before any outbound HTTP request. If a non-None value is
    returned, refuse the request and surface the reason to the caller.

    Parameters
    ----------
    url:
        The full URL string to validate.
    allow_loopback_ports:
        Ports on the LOOPBACK interface (127.0.0.1 / ::1 / localhost) that are
        permitted despite the loopback block — used so the agent can verify a dev
        server IT started on that port. This ONLY relaxes loopback; private-LAN,
        link-local, and cloud-metadata targets stay blocked regardless.

    Returns
    -------
    str | None
        Human-readable reason if the URL is blocked, ``None`` if safe.
    """
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

    hostname = (parsed.hostname or "").lower().strip(".")
    if not hostname:
        return "URL has no hostname"

    # Scoped loopback allowance: a loopback host on a port the agent started. Never
    # opens up cloud-metadata / broadcast aliases or private-LAN addresses.
    try:
        port = parsed.port
    except ValueError:
        port = None

    def _loopback_permitted(host_is_loopback: bool) -> bool:
        return bool(
            host_is_loopback
            and allow_loopback_ports
            and port is not None
            and int(port) in allow_loopback_ports
        )

    # 1. Known-blocked hostnames (fast path, no DNS needed).
    if hostname in _BLOCKED_HOSTNAMES:
        if hostname in _LOOPBACK_HOSTNAMES and _loopback_permitted(True):
            return None
        return f"blocked hostname: {hostname}"

    # 2. Try to parse the hostname directly as an IP first.
    try:
        direct_reason = _ip_is_blocked(hostname)
        if direct_reason:
            if _is_loopback_ip(hostname) and _loopback_permitted(True):
                return None
            return direct_reason
    except Exception:
        pass

    # 3. DNS resolution — block if ANY resolved address is private.
    addrs = _resolve_host(hostname)
    for addr in addrs:
        reason = _ip_is_blocked(addr)
        if reason:
            if _is_loopback_ip(addr) and _loopback_permitted(True):
                continue
            return f"hostname {hostname!r} resolves to {reason}"

    return None


def _is_loopback_ip(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False
