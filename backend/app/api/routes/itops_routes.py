"""IT Operations HTTP API — Phase 0 SSH vertical v1 (enrollment + verify).

All routes are gated on the `itops` feature flag: OFF → 404 (disabled). v1 works
only with an EXISTING OpenSSH alias and never touches ~/.ssh, known_hosts, keys,
ssh-agent, Credential Manager, or any secret value.

Flow: preview (ssh -G + an OBSERVED, advisory fingerprint) → enroll (saves a
`draft` asset + an unverified profile, auth_ref=NULL; grants the model NO host
access — there is no SSH allowlist step here) → verify (by saved profile_id ONLY,
never a host from the browser; success promotes the asset to `enabled`, failure
leaves it `draft`). Nothing is auto-deleted; an unverified profile stays visible.

The out-of-band fingerprint comparison is the user's, made in the UI. The API
ENFORCES that attestation: enroll requires `fingerprint_reviewed=True` (the user
asserts they compared the OBSERVED fingerprint against a trusted source); a request
without it is 422. This is an attestation of a human action, NOT cryptographic
proof — the server cannot verify the comparison actually happened, only that the
caller claims it did. Saving/verifying a profile does NOT let the model connect over
it — that needs a separate scope/approval layer.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/itops", tags=["it-operations"])


def _require_flag() -> None:
    from app.application.feature_flags import flag_enabled
    if not flag_enabled("itops"):
        raise HTTPException(status_code=404, detail="itops feature is disabled")


def _store():
    from app.infrastructure.it_ops import store
    store.init_db()      # idempotent; only reached when the flag is on
    return store


# ── request models ──────────────────────────────────────────────────────────

class SshPreviewRequest(BaseModel):
    ssh_alias: str = Field(..., description="An existing OpenSSH Host alias (~/.ssh/config)")


class SshEnrollRequest(BaseModel):
    model_config = {"extra": "forbid"}
    label: str
    ssh_alias: str
    kind: str = "linux"
    # User attestation that they compared the OBSERVED host-key fingerprint against a
    # trusted out-of-band source. Required and must be exactly True — a missing or
    # false value is a 422. Not cryptographic proof; an attestation of a human action.
    fingerprint_reviewed: Literal[True]


class VerifyRequest(BaseModel):
    model_config = {"extra": "forbid"}
    profile_id: str = Field(..., description="A SAVED connection profile id — never a raw host")


# ── endpoints ─────────────────────────────────────────────────────────────

@router.post("/ssh/preview")
def ssh_preview(payload: SshPreviewRequest) -> dict[str, Any]:
    """Resolve the alias's effective config (ssh -G, no network) and an OBSERVED
    host-key fingerprint (advisory). Saves nothing; changes no allowlist."""
    _require_flag()
    from app.application.it_ops import ssh_enroll
    if not ssh_enroll.alias_ok(payload.ssh_alias):
        raise HTTPException(status_code=400, detail="invalid ssh alias")
    effective = ssh_enroll.resolve_alias(payload.ssh_alias)
    if not effective.get("ok"):
        raise HTTPException(status_code=400, detail=effective.get("error", "alias resolve failed"))
    fp = ssh_enroll.observe_fingerprint(effective["hostname"], effective["port"])
    return {"ok": True, "effective": effective, "observed_fingerprint": fp}


@router.post("/ssh/enroll")
def ssh_enroll_asset(payload: SshEnrollRequest) -> dict[str, Any]:
    """Save a DRAFT asset + unverified SSH profile for an EXISTING alias. Requires
    `fingerprint_reviewed=True` — the user's attestation that they compared the
    OBSERVED fingerprint out-of-band (enforced by the request model; missing/false →
    422). This does NOT grant the model any host access (the SSH allowlist is
    untouched) and stores no secret (auth_ref=NULL). The stored fingerprint is what
    the SERVER observed now — advisory, not proof. `draft` until a successful verify."""
    _require_flag()
    from app.application.it_ops import ssh_enroll

    if not ssh_enroll.alias_ok(payload.ssh_alias):
        raise HTTPException(status_code=400, detail="invalid ssh alias")
    effective = ssh_enroll.resolve_alias(payload.ssh_alias)
    if not effective.get("ok"):
        raise HTTPException(status_code=400, detail=effective.get("error", "alias resolve failed"))
    # server-side OBSERVED fingerprint (advisory) — never a client-supplied "proof".
    observed = ssh_enroll.observe_fingerprint(effective["hostname"], effective["port"])

    store = _store()
    try:
        asset = store.upsert_asset(
            asset_id=f"ssh-{payload.ssh_alias}", label=payload.label, kind=payload.kind,
            endpoint=f"{effective['hostname']}:{effective['port']}", lifecycle_state="draft")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    profile = store.put_connection_profile(
        profile_id=f"prof-{uuid.uuid4().hex[:12]}", asset_id=asset["asset_id"], transport="ssh",
        user=effective.get("user", ""), auth_ref=None, ssh_alias=payload.ssh_alias,
        host_key_fingerprint="; ".join(observed.get("fingerprints", [])),  # observed, advisory
        os_platform_meta={"effective": effective, "observed_fingerprint": observed,
                          "fingerprint_reviewed": True},  # user attestation (audit trail)
        last_health={"status": "unverified"})
    return {"ok": True, "asset": asset, "profile": profile}


@router.post("/verify")
def verify_profile(payload: VerifyRequest) -> dict[str, Any]:
    """Verify a SAVED profile by id (never a browser-supplied host). Runs SSH with
    STRICT host-key checking on the STORED alias. Success → the asset becomes
    `enabled`; failure → it stays/returns to `draft`. Updates last_health only."""
    _require_flag()
    from app.application.it_ops import ssh_enroll

    store = _store()
    profile = store.get_connection_profile(payload.profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")
    alias = profile.get("ssh_alias") or ""
    # the alias comes from the STORED profile (validated at enroll), not the request;
    # re-check the shape defensively so a fixed `ssh <alias> hostname` argv is safe.
    if not ssh_enroll.alias_ok(alias):
        raise HTTPException(status_code=400, detail="stored alias is not a valid token")

    result = ssh_enroll.verify_alias(alias)
    from time import time as _time
    if result.get("ok"):
        health = {"status": "verified", "hostname": result.get("output", ""), "at": _time()}
        store.set_asset_lifecycle(profile["asset_id"], "enabled")   # trusted only after exit 0
    else:
        health = {"status": "unverified", "reason": result.get("reason", "verify failed"),
                  "at": _time()}
        store.set_asset_lifecycle(profile["asset_id"], "draft")     # failure leaves it draft
    store.set_profile_health(payload.profile_id, health)
    return {"ok": bool(result.get("ok")), "profile_id": payload.profile_id,
            "health": health, "detail": result}


@router.get("/assets")
def list_assets() -> dict[str, Any]:
    """Assets + their connection profiles (including unverified/incomplete ones,
    which stay visible for manual action). No secret values are ever returned."""
    _require_flag()
    store = _store()
    assets = store.list_assets()
    by_asset: dict[str, list] = {}
    for p in store.list_connection_profiles():
        by_asset.setdefault(p["asset_id"], []).append(p)
    return {"ok": True, "assets": [{**a, "profiles": by_asset.get(a["asset_id"], [])}
                                   for a in assets]}
