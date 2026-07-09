"""IT-Ops secret vault — Windows Credential Manager backed.

`put_secret(raw) -> secret_ref` writes the value into Credential Manager and
records only STATE in the it_ops store. `resolve(secret_ref)` reads the value back
(runtime-only, called inside dispatch after gates). `state` / `revoke` manage the
lifecycle. The value never touches the it_ops DB, args, journals, events, or the
model — only the opaque `secret_ref` does.
"""
from __future__ import annotations

import uuid

from app.infrastructure.it_ops import store as _store
from app.infrastructure.secrets import wincred


class SecretUnavailable(RuntimeError):
    """The secret value could not be resolved (absent / revoked / vault down)."""


def _new_ref() -> str:
    return "sref_" + uuid.uuid4().hex


def put_secret(*, kind: str, value: str, asset_id: str | None = None,
               lifecycle: str = "temporary") -> str:
    """Store *value* in Credential Manager; persist only state. Returns secret_ref.

    The raw value is used ONCE here to write the OS vault and is not retained,
    logged, or persisted anywhere else."""
    if not value:
        raise ValueError("value is required")
    secret_ref = _new_ref()
    wincred.write_secret(secret_ref, value)          # value → Credential Manager only
    _store.put_secret_ref(secret_ref=secret_ref, kind=kind, backend="wincred",
                          asset_id=asset_id, lifecycle=lifecycle)
    return secret_ref


def resolve(secret_ref: str) -> str:
    """Runtime-only: read the value from Credential Manager. Fail-closed if the ref
    is unknown or revoked. NEVER call from an LLM-facing tool."""
    st = _store.secret_ref_state(secret_ref)
    if not st:
        raise SecretUnavailable(f"unknown secret_ref: {secret_ref}")
    if st.get("lifecycle") == "revoked":
        raise SecretUnavailable(f"secret_ref revoked: {secret_ref}")
    value = wincred.read_secret(secret_ref)
    if value is None:
        raise SecretUnavailable(f"secret_ref not in vault: {secret_ref}")
    return value


def state(secret_ref: str) -> dict | None:
    """STATE only — never a value."""
    return _store.secret_ref_state(secret_ref)


def revoke(secret_ref: str) -> None:
    """Delete from Credential Manager + mark revoked. A resolve after this fails."""
    try:
        wincred.delete_secret(secret_ref)
    except wincred.WinCredUnavailable:
        pass  # still mark revoked in state so it fails closed
    _store.mark_secret_revoked(secret_ref)
