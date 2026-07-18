# PHASE-7-device-adapters — Device Adapters

- **Layer:** Core ops
- **Status:** ready-for-agent (Phase 7A v2 after static-contract recon)
- **Depends on:** PHASE-6

## Scope
- Generic network-device asset model; MikroTik = one adapter (reuse existing mikrotik MCP), alongside Linux/Windows and future devices.
- Read inventory/export first.
- Firewall/NAT/routing changes = separate ChangeRuns with export/snapshot, approval, post-change connectivity test, rollback.

### Phase 7A — bounded MikroTik read-only core

- Work in an isolated worktree from committed `main`; the primary worktree contains
  unrelated green WIP and must not be edited.
- Ground the configured MikroTik MCP tool names and response envelope from static
  configuration/source metadata. Do not invoke the router or perform a live smoke.
- Reuse the existing MCP transport. No RouterOS client, SSH transport, HTTP client,
  credential handling, second MCP roster, routes, tool registration, provider wiring or UI.
- Recon confirmed `mikromcp@1.7.0`: the result envelope and per-tool container keys are
  static, but RouterOS records have no `outputSchema`. The projector may use only fields
  referenced by MikroMCP's own v1.7.0 text renderers, treat every field as optional and
  report malformed/missing sections honestly. It must not claim schema validation.
- The v1 whitelist is narrower than those renderers:
  - system resource: `board-name`, `version`, `architecture`, `uptime`, `cpu-load`,
    `free-memory`, `total-memory`, `free-hdd-space`, `total-hdd-space`;
  - identity: `name`; license: `level`; routerboard: `model`, `firmware-type`,
    `current-firmware`; clock: `date`, `time`, `time-zone-name`;
  - interfaces: `name`, `type`, `running`, `disabled`, `mtu`;
  - routes: `dst-address`, `gateway`, `distance`, `routing-table`, `active`, `disabled`;
  - DNS: `servers` or `server`, `cache-size`, `cache-max-ttl`,
    `allow-remote-requests`;
  - DHCP servers: `name`, `interface`, `address-pool`, `lease-time`, `disabled`.
  `system.health` is omitted because MikroMCP renders its keys dynamically. Interface
  comments, serial numbers and software IDs are also omitted deliberately.
- Add only a pure bounded projector module and behavior tests in new files. It accepts
  raw MCP result envelopes for `get_system_status`, `list_interfaces`, `list_routes`,
  `get_dns_settings` and `list_dhcp_servers`, then emits a stable whitelist projection.
- IP addresses are explicitly unsupported in v1 because MikroMCP 1.7.0 has no read-only
  `/ip/address` tool. Output must say `coverage=partial` and include
  `missing_capabilities=["ip_addresses"]`; never call `manage_ip_address` to fill the gap.
- A missing expected tool result is listed in `unavailable_sections`; a present result
  with `isError=true`, no object `structuredContent`, a wrong container type or a
  non-object list item raises a typed projection error. Unknown tool results and unknown
  record fields are ignored.
- Cap strings/list sizes and drop credentials, raw exports, scripts, certificates,
  secrets, comments and arbitrary extra fields.
- Malformed payloads fail closed. No model-supplied command/path/host and no write action.

## Definition of Done (behavior, not labels)
- Static MCP envelope/container contract and renderer-referenced fields are cited by
  package path/tool metadata; output labels coverage partial and never claims outputSchema.
- Pure projector tests cover valid data, malformed types, caps, stable ordering and
  intentionally contaminated secret-bearing input.
- Focused tests and the UTF-8/BOM gate pass. Full suite and all live checks stay deferred.
- No existing integration file is modified in Phase 7A.

## Forbidden in this issue
Duplicating the MCP roster; a second device runtime; live router access; export/config
changes; firewall/NAT/routing writes; edits outside the two new core/test files.
