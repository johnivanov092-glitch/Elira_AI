"""IT Operations HTTP API — Phase 0 SSH vertical v1 (enrollment + verify).

All routes are gated on the `itops` feature flag: OFF → 404 (disabled). v1 works
only with an EXISTING OpenSSH alias and never touches ~/.ssh, known_hosts, keys,
ssh-agent, Credential Manager, or any secret value.

Flow: preview (ssh -G + observed fingerprint) → enroll (after the user confirms
the fingerprint out-of-band; adds the alias to the SSH allowlist and saves the
asset+profile as `unverified`) → verify (by saved profile_id ONLY — never a host
from the browser). Nothing is auto-deleted; an unverified profile stays visible.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

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
    confirmed_fingerprint: str = Field(
        ..., description="The host-key fingerprint the user VERIFIED out-of-band and confirms")


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
    """Save an asset + SSH connection profile for an EXISTING alias. The allowlist
    is extended ONLY here, after the user's explicit fingerprint confirmation. The
    profile starts `unverified` (nothing is trusted until verify runs)."""
    _require_flag()
    from app.application.it_ops import ssh_enroll
    from app.application.tool_providers import ssh_acl

    if not ssh_enroll.alias_ok(payload.ssh_alias):
        raise HTTPException(status_code=400, detail="invalid ssh alias")
    if not (payload.confirmed_fingerprint or "").strip():
        raise HTTPException(status_code=400,
                            detail="confirmed_fingerprint is required — confirm the host key out-of-band first")
    effective = ssh_enroll.resolve_alias(payload.ssh_alias)
    if not effective.get("ok"):
        raise HTTPException(status_code=400, detail=effective.get("error", "alias resolve failed"))

    store = _store()
    try:
        asset = store.upsert_asset(
            asset_id=f"ssh-{payload.ssh_alias}", label=payload.label, kind=payload.kind,
            endpoint=f"{effective['hostname']}:{effective['port']}", lifecycle_state="enabled")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # allowlist the alias ONLY now (explicit confirm) so the SSH provider/verify accept it
    hosts = ssh_acl.get_allowed_hosts()
    if payload.ssh_alias not in hosts:
        ssh_acl.set_allowed_hosts(hosts + [payload.ssh_alias])

    profile = store.put_connection_profile(
        profile_id=f"prof-{uuid.uuid4().hex[:12]}", asset_id=asset["asset_id"], transport="ssh",
        user=effective.get("user", ""), auth_ref=None, ssh_alias=payload.ssh_alias,
        host_key_fingerprint=payload.confirmed_fingerprint.strip(),
        os_platform_meta=effective, last_health={"status": "unverified"})
    return {"ok": True, "asset": asset, "profile": profile}


@router.post("/verify")
def verify_profile(payload: VerifyRequest) -> dict[str, Any]:
    """Verify a SAVED profile by id (never a browser-supplied host). Runs SSH with
    STRICT host-key checking; updates last_health. Does not modify anything else."""
    _require_flag()
    from app.application.it_ops import ssh_enroll
    from app.application.tool_providers import ssh_acl

    store = _store()
    profile = store.get_connection_profile(payload.profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")
    alias = profile.get("ssh_alias") or ""
    # defense in depth: only verify an allowlisted alias resolved from the STORED profile
    if not ssh_enroll.alias_ok(alias) or not ssh_acl.is_host_allowed(alias):
        raise HTTPException(status_code=403, detail="alias not in the SSH allowlist")

    result = ssh_enroll.verify_alias(alias)
    from time import time as _time
    health = ({"status": "verified", "hostname": result.get("output", ""), "at": _time()}
              if result.get("ok")
              else {"status": "unverified", "reason": result.get("reason", "verify failed"),
                    "at": _time()})
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
