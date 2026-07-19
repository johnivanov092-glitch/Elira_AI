"""Phase 7A — MikroTik read-only inventory projection (bounded partial v1).

Pure whitelist projector over raw MCP tools/call result envelopes produced by
the already-rostered ``mikrotik`` MCP server (mikromcp@1.7.0). The caller
fetches the envelopes through the EXISTING MCP transport and hands them in as
plain data; this module never talks to anything.

Contract basis — static evidence, NOT a machine-readable schema: mikromcp@1.7.0
declares no output schema for any tool, so the projection trusts only (a) the
``structuredContent`` envelope shape hard-coded in each tool handler and (b) the
record fields mikromcp's own text renderer references. Everything else is
unknown by construction: unknown tools and unknown record fields are dropped,
malformed containers fail closed. Coverage is honestly "partial":
mikromcp@1.7.0 has no read-only /ip/address tool, so IP address inventory is a
declared missing capability, never fabricated from other sections.

Explicitly NOT done here (v1):
- no transport: no MCP calls, no RouterOS client, no SSH/HTTP, no sockets,
  no subprocesses — the module imports only stdlib typing helpers;
- no credentials, secret refs, or secret handling of any kind;
- no tool registration, provider, route, or registry wiring;
- no ip_addresses section (missing read-only tool upstream);
- no write/change operations, no config/export passthrough.

Fail-closed: a tool-level error, a non-object ``structuredContent``, a missing/
mismatched ``routerId``, a wrong container type, a non-object list record, or a
complex value in a whitelisted scalar field raise MikrotikProjectionError with
a machine-readable ``reason``. Error text carries only structural names (tool /
section / whitelisted field), never payload values, so a poisoned payload can
leak neither through the projection nor through the error path.
"""
from __future__ import annotations

import heapq
import math
from collections.abc import Mapping
from typing import Any

CONTRACT = "mikromcp-renderer-v1.7.0"
COVERAGE = "partial"
MISSING_CAPABILITIES = ("ip_addresses",)

MAX_STRING_CHARS = 256
MAX_INTERFACES = 128
MAX_ROUTES = 256
MAX_DHCP_SERVERS = 128
MAX_DNS_SERVERS = 32


class MikrotikProjectionError(ValueError):
    """A rejected inventory payload. ``reason`` is machine-readable; ``detail``
    names the offending tool/section/whitelisted field — never payload values.
    ``http_status`` maps to the route response (502 = malformed upstream payload,
    400 = malformed caller input)."""

    def __init__(self, reason: str, detail: str = "", http_status: int = 502):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
        self.http_status = http_status


# ── whitelists: source field (RouterOS kebab-case, as referenced by the
#    mikromcp@1.7.0 text renderer) → output field (snake_case) ─────────────────

_SYSTEM_SUBSECTIONS: dict[str, dict[str, str]] = {
    "resource": {
        "board-name": "board_name",
        "version": "version",
        "architecture": "architecture",
        "uptime": "uptime",
        "cpu-load": "cpu_load",
        "free-memory": "free_memory",
        "total-memory": "total_memory",
        "free-hdd-space": "free_hdd_space",
        "total-hdd-space": "total_hdd_space",
    },
    "identity": {"name": "name"},
    "license": {"level": "level"},
    "routerboard": {
        "model": "model",
        "firmware-type": "firmware_type",
        "current-firmware": "current_firmware",
    },
    # "health" is deliberately absent: excluded by contract, never projected.
    "clock": {"date": "date", "time": "time", "time-zone-name": "time_zone_name"},
}

_INTERFACE_FIELDS = {
    "name": "name",
    "type": "type",
    "running": "running",
    "disabled": "disabled",
    "mtu": "mtu",
}

_ROUTE_FIELDS = {
    "dst-address": "dst_address",
    "gateway": "gateway",
    "distance": "distance",
    "routing-table": "routing_table",
    "active": "active",
    "disabled": "disabled",
}

_DNS_FIELDS = {
    "cache-size": "cache_size",
    "cache-max-ttl": "cache_max_ttl",
    "allow-remote-requests": "allow_remote_requests",
}

_DHCP_FIELDS = {
    "name": "name",
    "interface": "interface",
    "address-pool": "address_pool",
    "lease-time": "lease_time",
    "disabled": "disabled",
}

# tool → (output section, structuredContent container key, record whitelist, cap)
_LIST_SECTIONS: dict[str, tuple[str, str, dict[str, str], int]] = {
    "list_interfaces": ("interfaces", "interfaces", _INTERFACE_FIELDS, MAX_INTERFACES),
    "list_routes": ("routes", "routes", _ROUTE_FIELDS, MAX_ROUTES),
    "list_dhcp_servers": ("dhcp_servers", "servers", _DHCP_FIELDS, MAX_DHCP_SERVERS),
}

# Fixed processing order ⇒ deterministic unavailable/truncated lists.
_SECTION_ORDER: tuple[tuple[str, str], ...] = (
    ("get_system_status", "system"),
    ("list_interfaces", "interfaces"),
    ("list_routes", "routes"),
    ("get_dns_settings", "dns"),
    ("list_dhcp_servers", "dhcp_servers"),
)


def _scalar(value: Any, where: str) -> Any:
    """Pass str/bool/int/float through (strings clipped); anything else in a
    whitelisted field is fail-closed — a complex value there means the payload
    does not match the statically evidenced renderer contract."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise MikrotikProjectionError("field_non_finite", where)
        return value
    if isinstance(value, str):
        return value[:MAX_STRING_CHARS]
    raise MikrotikProjectionError("field_complex_type", where)


def _project_record(record: Any, fields: Mapping[str, str], where: str) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise MikrotikProjectionError("record_not_object", where)
    out: dict[str, Any] = {}
    for src, dst in fields.items():
        if src not in record:
            continue
        value = record[src]
        if value is None:          # JSON null ≈ absent; never projected
            continue
        out[dst] = _scalar(value, f"{where} field={dst}")
    return out


def _structured_content(tool: str, envelope: Any) -> Mapping[str, Any]:
    if not isinstance(envelope, Mapping):
        raise MikrotikProjectionError("result_not_object", f"tool={tool}")
    if envelope.get("isError"):
        # The error text is untrusted payload — name the tool, echo nothing.
        raise MikrotikProjectionError("tool_error", f"tool={tool}")
    content = envelope.get("structuredContent")
    if not isinstance(content, Mapping):
        raise MikrotikProjectionError("structured_content_not_object", f"tool={tool}")
    return content


def _check_router_id(content: Mapping[str, Any], tool: str, current: str | None) -> str:
    router_id = content.get("routerId")
    if not isinstance(router_id, str) or not router_id.strip():
        raise MikrotikProjectionError("router_id_invalid", f"tool={tool}")
    if current is not None and router_id != current:
        raise MikrotikProjectionError("router_id_mismatch", f"tool={tool}")
    return router_id


def _sort_key(fields: Mapping[str, str]) -> Any:
    order = tuple(fields.values())

    def scalar_key(value: Any) -> tuple[int, str]:
        if value is None:
            return (0, "")
        if isinstance(value, bool):
            return (1, "1" if value else "0")
        if isinstance(value, int):
            return (2, str(value))
        if isinstance(value, float):
            return (3, repr(value))
        return (4, value)

    def key(record: Mapping[str, Any]) -> tuple[tuple[int, str], ...]:
        return tuple(scalar_key(record.get(dst)) for dst in order)

    return key


def _project_list(
    content: Mapping[str, Any],
    tool: str,
    section: str,
    container_key: str,
    fields: Mapping[str, str],
    cap: int,
) -> tuple[list[dict[str, Any]], bool]:
    container = content.get(container_key)
    if not isinstance(container, list):
        raise MikrotikProjectionError("container_not_list", f"tool={tool} section={section}")
    records = (
        _project_record(item, fields, f"tool={tool} section={section}")
        for item in container
    )
    # Select the sorted prefix without materializing another unbounded list.
    selected = heapq.nsmallest(cap, records, key=_sort_key(fields))
    return selected, len(container) > cap


def _project_system(
    content: Mapping[str, Any], unavailable: list[str]
) -> dict[str, dict[str, Any]]:
    sections = content.get("sections")
    if not isinstance(sections, Mapping):
        raise MikrotikProjectionError(
            "container_not_object", "tool=get_system_status section=system"
        )
    out: dict[str, dict[str, Any]] = {}
    for name, fields in _SYSTEM_SUBSECTIONS.items():
        if name not in sections:
            unavailable.append(f"system.{name}")
            continue
        sub = sections[name]
        if not isinstance(sub, Mapping):
            raise MikrotikProjectionError(
                "container_not_object", f"tool=get_system_status section=system.{name}"
            )
        if "_error" in sub:
            # mikromcp marks a failed fetch as {"_error": ...}; the text is
            # untrusted — mark the subsection unavailable, echo nothing.
            unavailable.append(f"system.{name}")
            continue
        out[name] = _project_record(
            sub, fields, f"tool=get_system_status section=system.{name}"
        )
    return out


def _project_dns(content: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    settings = content.get("settings")
    if not isinstance(settings, Mapping):
        raise MikrotikProjectionError(
            "container_not_object", "tool=get_dns_settings section=dns"
        )
    out = _project_record(settings, _DNS_FIELDS, "tool=get_dns_settings section=dns")
    truncated = False
    raw = settings.get("servers")
    if raw is None:
        raw = settings.get("server")
    if raw is not None:
        if isinstance(raw, str):
            parts = raw.split(",", MAX_DNS_SERVERS)
            overflow = parts[MAX_DNS_SERVERS:]
            servers = [
                part.strip() for part in parts[:MAX_DNS_SERVERS] if part.strip()
            ]
            if overflow and any(
                char != "," and not char.isspace() for char in overflow[0]
            ):
                truncated = True
        elif isinstance(raw, list):
            if not all(isinstance(item, str) for item in raw):
                raise MikrotikProjectionError(
                    "dns_servers_invalid", "tool=get_dns_settings section=dns"
                )
            servers = raw[:MAX_DNS_SERVERS]
            if len(raw) > MAX_DNS_SERVERS:
                truncated = True
        else:
            raise MikrotikProjectionError(
                "dns_servers_invalid", "tool=get_dns_settings section=dns"
            )
        # Resolver order is semantic (primary/secondary) and already a
        # deterministic function of the input — preserved, not re-sorted.
        out["servers"] = [server[:MAX_STRING_CHARS] for server in servers]
    return out, truncated


def project_mikrotik_inventory(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Project raw MCP result envelopes (keyed by mikromcp tool name) into the
    bounded, whitelist-only inventory dict. Unknown tool keys are ignored;
    missing expected tools are reported in ``unavailable_sections``; a section
    cut by a local cap OR paginated upstream (hasMore=true) is reported in
    ``truncated_sections``; zero supported results fail closed. Raises
    MikrotikProjectionError."""
    if not isinstance(results, Mapping):
        raise MikrotikProjectionError("results_not_object", http_status=400)
    if not any(tool in results for tool, _ in _SECTION_ORDER):
        raise MikrotikProjectionError("no_supported_results", http_status=400)

    unavailable: list[str] = []
    truncated: list[str] = []
    sections: dict[str, Any] = {}
    router_id: str | None = None

    for tool, section in _SECTION_ORDER:
        if tool not in results:
            unavailable.append(section)
            continue
        content = _structured_content(tool, results[tool])
        router_id = _check_router_id(content, tool, router_id)
        locally_truncated = False
        if tool == "get_system_status":
            sections[section] = _project_system(content, unavailable)
        elif tool == "get_dns_settings":
            sections[section], locally_truncated = _project_dns(content)
        else:
            _, container_key, fields, cap = _LIST_SECTIONS[tool]
            sections[section], locally_truncated = _project_list(
                content, tool, section, container_key, fields, cap
            )
        # hasMore=true means the upstream itself paginated: the section is
        # incomplete no matter what local caps did. Strict `is True` — envelope
        # metadata is outside the whitelist, a malformed value is ignored, never
        # trusted. Checked for every section, not just paginated list tools.
        if locally_truncated or content.get("hasMore") is True:
            truncated.append(section)

    return {
        "contract": CONTRACT,
        "coverage": COVERAGE,
        "missing_capabilities": list(MISSING_CAPABILITIES),
        "unavailable_sections": unavailable,
        "truncated_sections": truncated,
        "router_id": (router_id or "")[:MAX_STRING_CHARS],
        **sections,
    }
