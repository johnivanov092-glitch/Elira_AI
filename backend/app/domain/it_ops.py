"""IT Operations domain types — pure dataclasses + enums, no I/O.

Lives in the domain layer per ARCHITECTURE.md (pure types, no infrastructure).
Terminology fixed in docs/IT_OPERATIONS_PLAN.md §1. TWO status axes that never
merge: the task axis `completion_status` (confirmed|partial|failed|unverified|n/a,
unchanged, from CriteriaTracker) and the lifecycle axis `change_run_status`.

The tuples below are the CANONICAL enums; the store validates against them at its
boundary (an invalid value is rejected before SQL).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ASSET_KINDS = (
    "linux", "windows", "network_device", "local_workspace",
    "ssh_config_scope", "database",
)
ASSET_LIFECYCLE = ("draft", "enabled", "disabled", "revoked")
TRANSPORTS = ("ssh", "winrm", "mcp", "local")
SECRET_KINDS = ("password", "private_key", "token", "connection_string")
# New writes use the application-owned portable vault. ``wincred`` remains only
# so existing metadata can be discovered and explicitly migrated in place.
SECRET_BACKENDS = ("portable_v1", "wincred")
# Lifecycle includes the internal saga states: `provisioning` (recovery record
# written BEFORE the credential), `cleanup_pending` (credential may exist but
# provisioning did not complete), and `recovering` (a recovery pass has atomically
# CLAIMED an incomplete record — ownership is exclusive so put_secret and recovery
# can never both finalize the same ref). resolve() is fail-closed for all three.
SECRET_LIFECYCLE = (
    "provisioning", "temporary", "persistent", "rotated", "revoked",
    "cleanup_pending", "recovering", "recovery_failed",
)
# Origin binds a secret_ref to the path that created it. Auto-recovery is a
# COMPENSATING action of the secure-intake path only — recovery may touch an
# incomplete record ONLY when its origin is `secure_intake`. Rows migrated in from
# a pre-origin schema get `legacy_unbound` so startup never auto-deletes them.
SECRET_ORIGINS = ("secure_intake", "legacy_unbound")
# Only put_secret_ref (the intake path) may CREATE a record — and only as
# secure_intake. legacy_unbound is set solely by the v1→v2 migration.
SECRET_INTAKE_ORIGINS = ("secure_intake",)
# What a caller may REQUEST as the final state of put_secret (internal saga states
# are not requestable).
SECRET_REQUESTABLE_LIFECYCLE = ("temporary", "persistent")
# Lifecycles whose value may be resolved (a complete, live secret).
SECRET_RESOLVABLE_LIFECYCLE = ("temporary", "persistent", "rotated")
ROLLBACK_KINDS = ("automatic", "manual", "none")

# lifecycle axis — the ChangeRun's own state machine (NOT the task axis)
CHANGE_RUN_STATUS = (
    "planned", "approved", "snapshotted", "applied", "committed",
    "applied_pending_verification", "rollback_in_progress",
    "rolled_back", "rollback_failed",
)
# task axis — unchanged, mirrors CriteriaTracker.completion_status()
COMPLETION_STATUS = ("confirmed", "partial", "failed", "unverified", "n/a")


@dataclass
class Asset:
    asset_id: str
    label: str
    kind: str
    endpoint: str = ""
    tags: list[str] = field(default_factory=list)
    owner_scope: str = ""
    lifecycle_state: str = "draft"


@dataclass
class ConnectionProfile:
    profile_id: str
    asset_id: str
    transport: str
    user: str = ""
    auth_ref: str | None = None            # == secret_ref (nullable = key-only)
    ssh_alias: str = ""
    host_key_fingerprint: str = ""
    os_platform_meta: dict[str, Any] = field(default_factory=dict)
    last_health: dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot:
    snapshot_id: str
    change_run_id: str
    asset_id: str
    before_state: str = ""
    artifact_path: str = ""
    content_hash: str = ""
    captured_at: float = 0.0
    rollback_ref: str = ""
    restored_at: float | None = None


@dataclass
class Evidence:
    evidence_id: str
    run_id: str
    target_identity: str
    scanner_vantage: str                    # 'local' | asset_id
    operation: str
    change_run_id: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    exit_status: str = ""
    captured_at: float = 0.0


@dataclass
class ChangeRun:
    change_run_id: str
    run_id: str
    asset_id: str
    plan: dict[str, Any] = field(default_factory=dict)
    snapshot_id: str | None = None
    rollback_kind: str = "none"             # automatic|manual|none (adapter-declared)
    change_run_status: str = "planned"      # LIFECYCLE axis
    completion_status: str | None = None    # TASK axis snapshot (never merged in)


@dataclass
class SecretRef:
    """STATE + metadata only; secret ciphertext belongs to the portable vault."""
    secret_ref: str
    kind: str
    backend: str = "portable_v1"
    asset_id: str | None = None
    lifecycle: str = "temporary"
