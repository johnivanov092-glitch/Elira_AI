"""IT Operations HTTP API — Phase 0 SSH vertical v1 (enrollment + verify).

All routes are gated on the `itops` feature flag: OFF → 404 (disabled). v1 works
only with an EXISTING OpenSSH alias and never touches ~/.ssh, known_hosts, keys,
ssh-agent, Credential Manager, or any secret value.

Flow: preview (ssh -G + an OBSERVED, advisory fingerprint) → enroll (saves a
`draft` asset + an unverified profile, auth_ref=NULL; grants the model NO host
access — there is no SSH allowlist step here) → verify (by saved profile_id ONLY,
never a host from the browser; success promotes the asset to `enabled`, failure
leaves it `draft`). Nothing is auto-deleted; an unverified profile stays visible.

The out-of-band fingerprint comparison is the user's, made in the UI. `fingerprint_reviewed=True`
is REQUIRED on enroll (missing/false → 422), and enroll refuses (409) when the server
cannot observe a host key to compare against — you cannot attest to a key you were
never shown. But this flag only records the user's CLAIM: it is a user attestation,
NOT runtime proof — the server cannot prove a human actually compared anything, only
that the caller asserted it. Saving/verifying a profile does NOT let the model connect
over it — that needs a separate scope/approval layer.
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


class DiagnosticsStartRequest(BaseModel):
    model_config = {"extra": "forbid"}
    profile_id: str = Field(..., description="A SAVED, VERIFIED (enabled) connection profile id")
    # The client picks an ADAPTER (a fixed enum), never a tool name. The server maps
    # adapter → tool via _ADAPTER_TOOL and binds the scope to exactly that tool.
    adapter: Literal["healthcheck", "linux_inventory"] = "healthcheck"


# Server-side adapter → tool table. The client never supplies a tool name; this is
# the ONLY source of a scope's allowed_tool.
_ADAPTER_TOOL: dict[str, str] = {
    "healthcheck": "itops_ssh_healthcheck",
    "linux_inventory": "itops_linux_inventory",
}
# Adapters that require a specific asset kind.
_ADAPTER_REQUIRES_KIND: dict[str, str] = {"linux_inventory": "linux"}


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
    `fingerprint_reviewed=True` — the user's CLAIM that they compared the observed
    fingerprint out-of-band (a user attestation, not proof; missing/false → 422). If
    the server cannot observe a host key now there is nothing to attest to → 409 and
    nothing is written. Grants the model NO host access (the SSH allowlist is untouched)
    and stores no secret (auth_ref=NULL). The stored fingerprint is what the SERVER
    observed now — advisory. `draft` until a successful verify."""
    _require_flag()
    from app.application.it_ops import ssh_enroll

    if not ssh_enroll.alias_ok(payload.ssh_alias):
        raise HTTPException(status_code=400, detail="invalid ssh alias")
    effective = ssh_enroll.resolve_alias(payload.ssh_alias)
    if not effective.get("ok"):
        raise HTTPException(status_code=400, detail=effective.get("error", "alias resolve failed"))
    # server-side OBSERVED fingerprint (advisory) — never a client-supplied "proof".
    observed = ssh_enroll.observe_fingerprint(effective["hostname"], effective["port"])
    # Nothing to attest to if the server could not observe a host key now — refuse
    # BEFORE writing anything, so a fingerprint_reviewed=True over an empty observation
    # can never be persisted.
    if not observed.get("ok") or not observed.get("fingerprints"):
        raise HTTPException(status_code=409,
                            detail="host key could not be observed now — nothing to attest to")

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


@router.post("/diagnostics/start")
def diagnostics_start(payload: DiagnosticsStartRequest) -> dict[str, Any]:
    """Start ONE scoped read-only diagnostic run for a saved, VERIFIED profile.

    The client picks an ADAPTER (enum), never a tool name; the SERVER maps it to a
    tool via _ADAPTER_TOOL, mints the run_id, and binds a read-only operation scope
    (run_id -> profile_id, allowed_tool, TTL) BEFORE the run — the model never
    chooses the profile or the tool. The caller then opens POST /api/code-agent/stream
    with THIS run_id and the returned message; inside that run the executor allows
    only tool_search + that ONE bound adapter tool for this exact profile (everything
    else, including other itops adapters, is blocked). The lockdown is sticky: a TTL
    expiry blocks the tool call but never re-opens other tools; the scope is dropped
    when the run ends (finally/cancel/error) or is swept. A draft (unverified) or
    unknown profile — or an adapter whose required asset kind does not match — is
    refused here; nothing is bound."""
    _require_flag()
    import uuid
    from app.application.agent_kernel import operation_scope

    store = _store()
    profile = store.get_connection_profile(payload.profile_id)
    if not profile or profile.get("transport") != "ssh":
        raise HTTPException(status_code=404, detail="ssh profile not found")
    asset = store.get_asset(str(profile.get("asset_id") or ""))
    if not asset or asset.get("lifecycle_state") != "enabled":
        raise HTTPException(status_code=409, detail="profile is not verified/enabled — verify it first")
    # Adapter → tool is server-owned; some adapters require a specific asset kind.
    tool = _ADAPTER_TOOL[payload.adapter]
    need_kind = _ADAPTER_REQUIRES_KIND.get(payload.adapter)
    if need_kind and asset.get("kind") != need_kind:
        raise HTTPException(status_code=409,
                            detail=f"{payload.adapter} requires a {need_kind} asset")

    run_id = f"itops-diag-{uuid.uuid4().hex}"
    # bind the scope to EXACTLY this one adapter tool (not "any itops tool")
    operation_scope.bind_scope(run_id, payload.profile_id, allowed_tool=tool)
    message = (
        "Выполни read-only диагностику сохранённого подключения. Активируй инструмент "
        f"{tool} через tool_search, затем вызови его РОВНО ОДИН РАЗ с "
        f'profile_id="{payload.profile_id}" и покажи результат. Не вызывай никакие '
        "другие инструменты — это ограниченный диагностический запуск."
    )
    return {"ok": True, "run_id": run_id, "profile_id": payload.profile_id,
            "adapter": payload.adapter, "tool": tool,
            "message": message, "ttl_seconds": operation_scope.DEFAULT_TTL_SECONDS}


@router.get("/evidence/runs")
def evidence_runs(limit: int = 50) -> dict[str, Any]:
    """Read-only summary of recent diagnostic runs (target, adapter, time, ok/failed/
    unsupported). No SSH, no model, no host changes — just reads the evidence table."""
    _require_flag()
    store = _store()
    return {"ok": True, "runs": store.list_evidence_runs(limit=limit)}


# The ONLY result fields the public evidence API may expose. A hard whitelist so a
# record whose `result` was polluted with a secret (auth_ref/password/…) can never
# leak through this read API, regardless of what was stored.
_EVIDENCE_RESULT_FIELDS = ("alias", "command", "status", "stdout", "stderr")


def _public_evidence(ev: dict[str, Any]) -> dict[str, Any]:
    res = ev.get("result") or {}
    return {
        "evidence_id": ev.get("evidence_id"), "run_id": ev.get("run_id"),
        "target_identity": ev.get("target_identity"), "scanner_vantage": ev.get("scanner_vantage"),
        "operation": ev.get("operation"), "exit_status": ev.get("exit_status"),
        "captured_at": ev.get("captured_at"),
        "result": {k: res.get(k) for k in _EVIDENCE_RESULT_FIELDS if k in res},
    }


@router.get("/evidence")
def evidence_detail(run_id: str) -> dict[str, Any]:
    """Read-only evidence for ONE run (run_id required). Returns each command's result
    projected to a hard whitelist (alias/command/status/stdout/stderr) — never a
    secret or auth_ref, even if the stored result contained one."""
    _require_flag()
    if not str(run_id or "").strip():
        raise HTTPException(status_code=422, detail="run_id is required")
    store = _store()
    return {"ok": True, "run_id": run_id,
            "evidence": [_public_evidence(e) for e in store.list_evidence(run_id)]}
