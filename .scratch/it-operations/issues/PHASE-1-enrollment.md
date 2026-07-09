# PHASE-1-enrollment — Connection Enrollment

- **Layer:** Core ops
- **Status:** planned
- **Depends on:** PHASE-0

## Scope
- Secure connection card (UI) + secure intake API; secret fields POST to the hardened endpoint, model gets only secret_ref.
- SSH key inventory: public keys + paths only (private keys never read/copied/shown).
- Password bootstrap -> install selected public key -> verify key login -> delete temporary password by default; persistent-password fallback only after explicit opt-in.
- Atomic asset/profile/ACL save; connection test; host-key fingerprint approval before permanent save.
- Existing SSH provider stays the canonical transport after enrollment.

## Definition of Done (behavior, not labels)
- Raw secret absent from chat storage, journal, events, model messages, approval cards, logs.
- Key-bootstrap canary (password -> key -> temp-secret-deleted).
- Failed enrollment leaves NO enabled asset and NO dangling secret.

## Forbidden in this issue
No sshpass, no password in argv, no private key in tool output, no second SSH provider.
