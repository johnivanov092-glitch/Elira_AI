# TRACK-10-identity — Identity and Access

- **Layer:** Platform ops
- **Status:** planned (not now)
- **Depends on:** PHASE-0, PHASE-2; secret_ref lifecycle

## Scope
- Local users/groups, SSH keys, sudo, Windows local accounts.
- AD/LDAP/Entra/SSO as SEPARATE named adapters - never a generic LDAP shell.
- Inspect first; changes require approval + before/after membership evidence + rollback + revoke flow.

## Definition of Done (behavior, not labels)
- Inspect read-only canary; a membership change shows before/after evidence and rolls back on failure.

## Forbidden in this issue
Generic LDAP shell.
