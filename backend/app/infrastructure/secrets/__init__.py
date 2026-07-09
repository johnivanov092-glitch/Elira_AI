"""IT Operations secret vault (infrastructure layer).

The secret VALUE lives ONLY in Windows Credential Manager (per-user). The
application never persists a value, ciphertext, or DPAPI blob — the it_ops store
holds only an opaque `secret_ref` + state. The model proposes actions referencing
a `secret_ref`; the runtime resolves the value INSIDE dispatch, after all gates.

Windows-only by design (Phase 0). Off-Windows the backend import fails closed and
the whole IT-Ops program is inert. A second backend would be a future ADR.
"""
