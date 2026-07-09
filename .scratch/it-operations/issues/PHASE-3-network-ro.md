# PHASE-3-network-ro — Network Inventory (read-only first)

- **Layer:** Core ops
- **Status:** planned
- **Depends on:** PHASE-2

## Scope
- Explicit scanner vantage: local PC or a named remote asset.
- Profiles: inventory-light, targeted-ports, TLS/DNS, route/neighbor.
- Strict CIDR/host/port/rate/timeout caps; no blind public ranges by default.
- Every finding tied to target IP, scanner vantage, time, probe (Evidence).
- CVE only after concrete service/version evidence; advisory with source.

## Definition of Done (behavior, not labels)
- Safe live canary on a user-owned lab subnet; wrong CIDR blocked.
- Report distinguishes unknown/filtered/closed/open and does not overclaim.

## Forbidden in this issue
Brute force, exploit, auth scans, packet evasion, broad UDP/full-port scans, remediation in v1; 'vulnerable' from a guessed banner.
