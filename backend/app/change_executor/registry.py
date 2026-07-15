"""Service-owned target registry.

The executor resolves EVERYTHING about a change target — host, port, remote user,
known_hosts, identity file, unit, operation — from this registry, keyed by an opaque
`target_id`. It is loaded from an executor-owned file (`ELIRA_CHANGE_REGISTRY_PATH`) that
the main Elira user cannot write. Nothing here comes from the main it_ops store or from
the IPC caller: a tampered profile/alias in the main DB can never redirect a change,
because the caller supplies only a `target_id` label.

The registry supports two deliberately narrow target kinds:
* `systemd_restart` — one fixed restart of a `.service` unit;
* `netdata_config` — one fixed typed change (`[global] update every = 1`) applied by the
  root-owned remote helper whose content hash is pinned here.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from ._frozen import unit_name_ok

SYSTEMD_RESTART = "systemd_restart"
NETDATA_CONFIG = "netdata_config"

_RESTART_OPERATION = "restart"
_NETDATA_OPERATION = "set_update_every_1"
_NETDATA_UNIT = "netdata.service"
_NETDATA_CONFIG_ID = "netdata-main"
_NETDATA_HELPER_PATH = "/usr/local/sbin/elira-netdata-config"


class RegistryError(RuntimeError):
    """The registry is missing/unreadable/invalid, or an unknown target was requested."""


@dataclass(frozen=True)
class Target:
    target_id: str
    host: str
    port: int
    remote_user: str
    known_hosts: str      # executor-owned path, pinned into StrictHostKeyChecking=yes
    identity_file: str    # executor-owned change key (ACL-denied to the main user)
    target_kind: str
    unit: str
    operation: str
    config_id: str = ""
    helper_path: str = ""
    helper_sha256: str = ""


def _validate(target_id: str, raw: dict) -> Target:
    if not isinstance(raw, dict):
        raise RegistryError(f"target {target_id!r}: not an object")
    host = str(raw.get("host") or "").strip()
    remote_user = str(raw.get("remote_user") or "").strip()
    known_hosts = str(raw.get("known_hosts") or "").strip()
    identity_file = str(raw.get("identity_file") or "").strip()
    target_kind = str(raw.get("target_kind") or SYSTEMD_RESTART).strip()
    unit = str(raw.get("unit") or "").strip()
    operation = str(raw.get("operation") or "").strip()
    try:
        port = int(raw.get("port", 22))
    except (TypeError, ValueError):
        raise RegistryError(f"target {target_id!r}: bad port")
    if not host or not remote_user or not known_hosts or not identity_file:
        raise RegistryError(f"target {target_id!r}: host/remote_user/known_hosts/identity_file required")
    if not (0 < port < 65536):
        raise RegistryError(f"target {target_id!r}: port out of range")
    if target_kind not in (SYSTEMD_RESTART, NETDATA_CONFIG):
        raise RegistryError(f"target {target_id!r}: unsupported target_kind {target_kind!r}")
    if not unit_name_ok(unit):
        raise RegistryError(f"target {target_id!r}: invalid unit {unit!r}")
    if target_kind == SYSTEMD_RESTART:
        if operation != _RESTART_OPERATION:
            raise RegistryError(f"target {target_id!r}: systemd operation must be {_RESTART_OPERATION!r}")
        config_id = helper_path = helper_sha256 = ""
    else:
        config_id = str(raw.get("config_id") or "").strip()
        helper_path = str(raw.get("helper_path") or "").strip()
        helper_sha256 = str(raw.get("helper_sha256") or "").strip().lower()
        if unit != _NETDATA_UNIT or operation != _NETDATA_OPERATION:
            raise RegistryError(
                f"target {target_id!r}: netdata config target must bind the fixed unit/operation")
        if config_id != _NETDATA_CONFIG_ID or helper_path != _NETDATA_HELPER_PATH:
            raise RegistryError(
                f"target {target_id!r}: netdata config id/helper path do not match the fixed contract")
        if len(helper_sha256) != 64 or any(c not in "0123456789abcdef" for c in helper_sha256):
            raise RegistryError(f"target {target_id!r}: helper_sha256 must be 64 lowercase hex chars")
    return Target(target_id=target_id, host=host, port=port, remote_user=remote_user,
                  known_hosts=known_hosts, identity_file=identity_file, target_kind=target_kind,
                  unit=unit, operation=operation, config_id=config_id,
                  helper_path=helper_path, helper_sha256=helper_sha256)


def load_registry(path: str | None = None) -> dict[str, Target]:
    """Load + validate the whole registry. Every target must be well-formed or the load
    fails closed (RegistryError) — a partially-valid registry is never used."""
    p = (path or os.environ.get("ELIRA_CHANGE_REGISTRY_PATH", "")).strip()
    if not p:
        raise RegistryError("ELIRA_CHANGE_REGISTRY_PATH is not set (executor-owned registry required)")
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RegistryError(f"registry unreadable/invalid: {exc}") from exc
    targets = data.get("targets") if isinstance(data, dict) else None
    if not isinstance(targets, dict) or not targets:
        raise RegistryError("registry has no targets")
    return {tid: _validate(str(tid), raw) for tid, raw in targets.items()}


def resolve(target_id: str, *, path: str | None = None) -> Target:
    """Resolve one target_id (fail-closed on unknown)."""
    reg = load_registry(path)
    t = reg.get(str(target_id or "").strip())
    if t is None:
        raise RegistryError(f"unknown target_id {target_id!r}")
    return t
