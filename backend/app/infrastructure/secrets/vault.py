"""IT-Ops secret vault — Windows Credential Manager backed, provisioning saga.

`put_secret` is a SAGA, not best-effort cleanup: a durable recovery record
(state=`provisioning`) is written BEFORE any credential exists, so a credential is
never untracked. Only after CredWrite AND the transition to the requested lifecycle
succeed is the secret complete. If any step fails, the record stays durable and
honestly reflects an incomplete state (`provisioning` / `cleanup_pending`), and a
`VaultProvisioningError` (carrying the secret_ref, never the value) is raised.
`recover_incomplete_secrets` is the recovery path for such records.

`resolve` is fail-closed for any non-complete lifecycle. The value lives only in
Credential Manager; the it_ops DB holds only the ref + state.
"""
from __future__ import annotations

import uuid

from app.domain import it_ops as _dom
from app.infrastructure.it_ops import store as _store
from app.infrastructure.secrets import wincred


class SecretUnavailable(RuntimeError):
    """The secret value could not be resolved (absent / revoked / incomplete / down)."""


class VaultProvisioningError(RuntimeError):
    """A secret could not be fully provisioned. Carries the secret_ref (opaque) so
    the caller can observe / recover — never the secret value."""

    def __init__(self, secret_ref: str, message: str):
        self.secret_ref = secret_ref
        super().__init__(f"{message} (secret_ref={secret_ref})")


def _new_ref() -> str:
    return "sref_" + uuid.uuid4().hex


def _best_effort_mark(secret_ref: str, lifecycle: str) -> None:
    """Try to record an incomplete state. If the store is down we can't update it,
    but the durable `provisioning` record already tracks the secret_ref — recovery
    will still find it."""
    try:
        _store.set_secret_lifecycle(secret_ref, lifecycle)
    except _store.StoreUnavailable:
        pass


def put_secret(*, kind: str, value: str, asset_id: str | None = None,
               lifecycle: str = "temporary") -> str:
    """Provision a secret as a saga. Accepts ANY non-empty string value verbatim
    (no trim/normalization — a short client credential is valid). Returns the
    secret_ref on full success; raises VaultProvisioningError (with the ref, no
    value) if provisioning does not complete."""
    if not isinstance(value, str) or value == "":
        raise ValueError("secret value must be a non-empty string")
    if lifecycle not in _dom.SECRET_REQUESTABLE_LIFECYCLE:
        raise ValueError(
            f"requested lifecycle must be one of {list(_dom.SECRET_REQUESTABLE_LIFECYCLE)}, "
            f"got {lifecycle!r}")

    secret_ref = _new_ref()

    # 1. Durable recovery record FIRST — before any credential exists. If this
    #    fails, nothing was written to the vault: propagate (nothing to track).
    _store.put_secret_ref(secret_ref=secret_ref, kind=kind, backend="wincred",
                          asset_id=asset_id, lifecycle="provisioning")

    # 2. Write the credential. It is now TRACKED by the provisioning record.
    try:
        wincred.write_secret(secret_ref, value)
    except wincred.WinCredUnavailable as exc:
        _best_effort_mark(secret_ref, "cleanup_pending")  # no credential; still tracked
        raise VaultProvisioningError(secret_ref, "credential write failed") from exc

    # 3. CONDITIONAL finalize: provisioning → requested lifecycle, atomically.
    #    Only a rowcount==1 transition is success. If a recovery pass has claimed
    #    the record (provisioning → recovering) or removed it, the CAS returns
    #    False and we must NOT report success — recovery owns the credential's
    #    teardown, so put_secret raises instead of returning a ref that is being
    #    torn down. A store outage mid-finalize leaves the durable provisioning
    #    record (TRACKED); recovery will handle it.
    try:
        finalized = _store.finalize_provisioning(secret_ref, lifecycle)
    except _store.StoreUnavailable as exc:
        raise VaultProvisioningError(
            secret_ref, "provisioning did not complete (store unavailable; tracked)") from exc
    if not finalized:
        raise VaultProvisioningError(
            secret_ref, "provisioning superseded by recovery (state conflict)")

    return secret_ref


def resolve(secret_ref: str) -> str:
    """Runtime-only: read the value from Credential Manager. Fail-closed for any
    non-complete lifecycle (unknown / provisioning / cleanup_pending / revoked) and
    for a missing credential. NEVER call from an LLM-facing tool."""
    st = _store.secret_ref_state(secret_ref)
    if not st:
        raise SecretUnavailable(f"unknown secret_ref: {secret_ref}")
    if st.get("lifecycle") not in _dom.SECRET_RESOLVABLE_LIFECYCLE:
        raise SecretUnavailable(
            f"secret_ref not resolvable (lifecycle={st.get('lifecycle')!r}): {secret_ref}")
    value = wincred.read_secret(secret_ref)     # raises on infra error; None on not-found
    if value is None:
        raise SecretUnavailable(f"secret_ref not in vault: {secret_ref}")
    return value


def state(secret_ref: str) -> dict | None:
    """STATE only — never a value."""
    return _store.secret_ref_state(secret_ref)


def revoke(secret_ref: str) -> None:
    """Delete from Credential Manager, THEN mark the state revoked. Honest:
    the value is deleted first (or confirmed already-absent); lifecycle becomes
    `revoked` ONLY after a confirmed delete/not-found. An infrastructure error
    propagates and the state is NOT marked revoked (a failed revoke can't look
    done)."""
    wincred.delete_secret(secret_ref)          # raises on infra error → NOT revoked
    _store.mark_secret_revoked(secret_ref)


def recover_incomplete_secrets() -> dict:
    """Race-safe, BOUNDED, origin-scoped recovery. A COMPENSATING action of the
    secure-intake path — it touches ONLY `secure_intake` records (legacy_unbound
    rows are never auto-deleted). At most MAX_RECOVERY_PER_START records per pass.

    Per candidate (fresh-incomplete OR a STALE recovering lease):
      * exhausted MAX_RECOVERY_ATTEMPTS → terminal `recovery_failed` (visible);
      * else atomically LEASE-CLAIM it (→ recovering, attempts+1, timestamped) so a
        concurrent put_secret/recovery can't both act on it; then delete the
        credential and the claimed record;
      * unclaimable (other owner / origin / exhausted) → skipped; a claimed record
        we couldn't finish is reset to cleanup_pending (retryable — and if that
        reset also fails, the stale lease is reclaimed next pass, so it can never
        hang forever).
    Returns opaque secret_refs / counts only, never a value; `capped` flags that
    more records remain than were processed this pass. All transitions CAS."""
    now = _store._now()
    stale_before = now - _store.LEASE_TTL_SECONDS
    limit = _store.MAX_RECOVERY_PER_START
    refs = _store.list_recoverable(stale_before, limit + 1)     # +1 to detect overflow
    capped = len(refs) > limit
    refs = refs[:limit]

    cleaned: list[str] = []
    failed: list[dict] = []
    skipped: list[str] = []
    exhausted: list[str] = []
    for r in refs:
        sref = r["secret_ref"]
        if int(r.get("recovery_attempts") or 0) >= _store.MAX_RECOVERY_ATTEMPTS:
            if _store.mark_recovery_failed(sref, stale_before):    # terminal, visible
                exhausted.append(sref)
            continue
        try:
            if not _store.claim_for_recovery(sref, stale_before):
                skipped.append(sref)
                continue
            wincred.delete_secret(sref)              # raises on infra error; False if absent
            if _store.delete_claimed_recovery(sref):
                cleaned.append(sref)
            else:
                failed.append({"secret_ref": sref, "error": "state conflict on record delete"})
        except (wincred.WinCredUnavailable, _store.StoreUnavailable) as exc:
            try:
                _store.set_secret_lifecycle(sref, "cleanup_pending")
            except _store.StoreUnavailable:
                pass                                 # stale lease reclaimed next pass
            failed.append({"secret_ref": sref, "error": str(exc)})
    return {"cleaned": cleaned, "failed": failed, "skipped": skipped,
            "exhausted": exhausted, "capped": capped}
