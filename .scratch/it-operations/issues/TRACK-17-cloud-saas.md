# TRACK-17-cloud-saas — Cloud and SaaS Adapters

- **Layer:** Delivery ops
- **Status:** planned (not now)
- **Depends on:** PHASE-2 (Scope); secret_ref

## Scope
- AWS/Azure/GCP, DNS providers, mail, monitoring SaaS as NAMED adapters only.
- Each: explicit account/subscription/project scope, secret_ref, read-only inventory mode + audited change mode.

## Definition of Done (behavior, not labels)
- A named adapter does read-only inventory in a scoped account; changes are audited and approval-gated.

## Forbidden in this issue
Arbitrary cloud API endpoint/token tool.
