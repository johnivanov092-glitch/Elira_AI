"""The change engine — the executor's apply/drift/post-check state machine.

Drives the store CAS transitions from real (or, in tests, fake) SSH results:
- plan: fresh inspect → precondition gate (loaded+active/running+MainPID>0) → snapshot +
  planned argv → mint capabilities → atomic plan+capabilities row;
- apply (only after a verified Telegram approve consumed the capability and CASed the run
  into `applying`): pre-apply drift check (must be the SAME MainPID identity, else
  aborted_before_apply with NO restart) → the fixed sudo restart → classify:
    exit 0 + post-inspect DEFINITE + active/running + MainPID CHANGED → applied,
    exit 0 + post-inspect DEFINITE but criterion not met            → postcheck_failed,
    a remote non-zero exit                                          → command_failed,
    SSH timeout / exit 255 / an INDEFINITE post-inspect             → apply_unknown;
- resolve: a fresh inspect handed to the store's fresh-inspect-gated resolution.

The config target delegates its bounded file transaction and one compensating rollback
to the pinned root helper. `runner`/`sleep`/`gen_token`/`clock` are injectable so every
path is deterministic without a host.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import time
from typing import Any, Callable

from . import config_change, database_change, registry, transport
from . import store as cs
from ._frozen import decode_console, parse_show_output

CAPABILITY_TTL = 300.0        # approval window: a stale plan can't be approved against drift
APPLY_DEADLINE_SECONDS = 360.0  # covers config apply + bounded verify/rollback + transport margin
POSTCHECK_ATTEMPTS = 6
POSTCHECK_INTERVAL = 2.0

Runner = Callable[[list[str], int], "transport.SshResult"]


class PreconditionError(RuntimeError):
    """The target is not in a state a change may be planned against."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(fields: dict[str, Any]) -> str:
    return json.dumps(fields, sort_keys=True, ensure_ascii=False)


def _has_required(fields: dict[str, Any]) -> bool:
    for k in ("id", "load_state", "active_state", "sub_state"):
        if not isinstance(fields.get(k), str) or not fields[k].strip():
            return False
    return isinstance(fields.get("main_pid"), int) and not isinstance(fields.get("main_pid"), bool)


def _inspect(target: "registry.Target", runner: Runner) -> tuple[bool, str, dict[str, Any]]:
    """Returns (definite, exit_status, fields). `definite` iff exit 0 AND the required
    fields parsed — anything else (non-zero, timeout, launch error, unparseable) is
    INDEFINITE, i.e. 'unknown', never a fabricated state."""
    try:
        res = runner(transport.inspect_argv(target), transport.INSPECT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return (False, "timeout", {})
    except OSError:
        return (False, "error", {})
    if res.exit_code != 0:
        return (False, str(res.exit_code), {})
    fields = si_parse(res.stdout)
    return (_has_required(fields), "0", fields)


def si_parse(raw: bytes) -> dict[str, Any]:
    return parse_show_output(decode_console(raw))


def _precondition_ok(fields: dict[str, Any]) -> bool:
    return (fields.get("load_state") == "loaded" and fields.get("active_state") == "active"
            and fields.get("sub_state") == "running"
            and isinstance(fields.get("main_pid"), int) and int(fields.get("main_pid") or 0) > 0)


def plan(target_id: str, *, registry_path: str | None = None, runner: Runner = transport.run,
         gen_token: Callable[[], str] | None = None, clock: Callable[[], float] | None = None) -> dict[str, Any]:
    """Plan a change: fresh inspect, precondition gate, snapshot, mint capabilities, and the
    atomic plan+capabilities row. Returns the change_run_id, the snapshot, the two RAW
    capability tokens (for the Telegram inline keyboard — never persisted/logged), and the
    exact planned argv. Refuses (PreconditionError) if the unit is not loaded+active/running
    +MainPID>0. May raise ActiveTargetConflict (→ 409) if the target is already active."""
    gen_token = gen_token or (lambda: secrets.token_urlsafe(12))
    clock = clock or time.time
    target = registry.resolve(target_id, path=registry_path)
    if target.target_kind == registry.SQLITE_MIGRATION:
        return _plan_database(target, gen_token=gen_token, clock=clock)
    if target.target_kind == registry.NETDATA_CONFIG:
        return _plan_config(target, runner=runner, gen_token=gen_token, clock=clock)
    definite, _exit, fields = _inspect(target, runner)
    if not definite or not _precondition_ok(fields):
        raise PreconditionError(
            f"target {target_id!r} is not in a plannable state (need loaded+active/running+MainPID>0)")
    planned_argv = transport.apply_argv(target)
    planned_argv_hash = _sha(" ".join(planned_argv))
    snapshot_hash = _sha(_canonical(fields))
    planned_binding = _canonical(transport.target_binding(target))   # host/port/user/unit/op + known_hosts hash
    approve_token, reject_token = gen_token(), gen_token()
    change_run_id = cs.new_change_run_id()
    cs.create_plan_with_capabilities(
        change_run_id=change_run_id, target_id=target.target_id, unit=target.unit,
        operation=target.operation, snapshot=_canonical(fields),
        snapshot_main_pid=int(fields["main_pid"]), snapshot_hash=snapshot_hash,
        planned_argv_hash=planned_argv_hash, planned_binding=planned_binding,
        approve_hash=_sha(approve_token), reject_hash=_sha(reject_token),
        capability_expires_at=clock() + CAPABILITY_TTL)
    return {"change_run_id": change_run_id, "snapshot": fields, "planned_argv": planned_argv,
            "approve_token": approve_token, "reject_token": reject_token}


def _plan_config(target: "registry.Target", *, runner: Runner,
                 gen_token: Callable[[], str], clock: Callable[[], float]) -> dict[str, Any]:
    definite, _exit, snapshot = config_change.inspect(target, runner)
    if (not definite or snapshot.get("before_sha256") == snapshot.get("planned_sha256")
            or not config_change.healthy(snapshot.get("service") or {}, target.unit)):
        raise PreconditionError(
            f"target {target.target_id!r} is not in a plannable config state")
    change_run_id = cs.new_change_run_id()
    planned_argv = transport.config_apply_argv(
        target, change_run_id=change_run_id,
        before_sha256=snapshot["before_sha256"], after_sha256=snapshot["planned_sha256"])
    planned_argv_hash = _sha(" ".join(planned_argv))
    snapshot_hash = _sha(_canonical(snapshot))
    planned_binding = _canonical(transport.target_binding(target))
    approve_token, reject_token = gen_token(), gen_token()
    cs.create_plan_with_capabilities(
        change_run_id=change_run_id, target_id=target.target_id, unit=target.unit,
        operation=target.operation, snapshot=_canonical(snapshot),
        snapshot_main_pid=int(snapshot["service"]["main_pid"]), snapshot_hash=snapshot_hash,
        planned_argv_hash=planned_argv_hash, planned_binding=planned_binding,
        approve_hash=_sha(approve_token), reject_hash=_sha(reject_token),
        capability_expires_at=clock() + CAPABILITY_TTL)
    return {"change_run_id": change_run_id, "snapshot": snapshot,
            "planned_argv": planned_argv, "approve_token": approve_token,
            "reject_token": reject_token}


def _plan_database(target: "registry.Target", *, gen_token: Callable[[], str],
                   clock: Callable[[], float]) -> dict[str, Any]:
    definite, _exit, snapshot = database_change.inspect(target)
    if not definite or not database_change.plannable(snapshot, target):
        raise PreconditionError(
            f"target {target.target_id!r} is not in a plannable database state")
    change_run_id = cs.new_change_run_id()
    planned_argv = database_change.planned_operation(target)
    planned_argv_hash = _sha(" ".join(planned_argv))
    snapshot_hash = _sha(_canonical(snapshot))
    planned_binding = _canonical(transport.target_binding(target))
    approve_token, reject_token = gen_token(), gen_token()
    cs.create_plan_with_capabilities(
        change_run_id=change_run_id,
        target_id=target.target_id,
        unit=target.unit,
        operation=target.operation,
        snapshot=_canonical(snapshot),
        snapshot_main_pid=0,
        snapshot_hash=snapshot_hash,
        planned_argv_hash=planned_argv_hash,
        planned_binding=planned_binding,
        approve_hash=_sha(approve_token),
        reject_hash=_sha(reject_token),
        capability_expires_at=clock() + CAPABILITY_TTL,
    )
    return {
        "change_run_id": change_run_id,
        "snapshot": snapshot,
        "planned_argv": planned_argv,
        "approve_token": approve_token,
        "reject_token": reject_token,
    }


def apply(change_run_id: str, *, registry_path: str | None = None, runner: Runner = transport.run,
          sleep: Callable[[float], None] = time.sleep) -> str:
    """Apply an already-approved (state `applying`) change and drive it to a terminal state.
    Returns the terminal status. Idempotent-safe: if the run is not `applying` (e.g. already
    swept), it does nothing and returns the current status."""
    cr = cs.get_change_run(change_run_id)
    if cr is None or cr["change_run_status"] != "applying":
        return cr["change_run_status"] if cr else "missing"
    token = cr["attempt_token"]
    target = registry.resolve(cr["target_id"], path=registry_path)

    # 0) immutable binding check — BEFORE any SSH. The current registry binding
    # (host/port/user/unit/op + known_hosts CONTENT hash) must equal the one captured at
    # plan; any drift (registry edited, host-key pin swapped) → aborted, no SSH.
    if not _binding_matches(cr, target):
        if target.target_kind == registry.SQLITE_MIGRATION:
            prefix = "database_change"
        else:
            prefix = "config_change" if target.target_kind == registry.NETDATA_CONFIG else "systemd_change"
        _evi(change_run_id, cr["target_id"], f"{prefix}:aborted_before_apply",
             {"reason": "target binding drift since plan"}, "")
        return _final(change_run_id, token, "aborted_before_apply", "target binding drift since plan")

    if target.target_kind == registry.SQLITE_MIGRATION:
        return _apply_database(cr, target)
    if target.target_kind == registry.NETDATA_CONFIG:
        return _apply_config(cr, target, runner=runner)

    # 1) pre-apply drift check — must still be the SAME MainPID identity from the plan.
    definite, exit_status, fields = _inspect(target, runner)
    same_identity = (definite and _precondition_ok(fields)
                     and int(fields.get("main_pid") or 0) == int(cr["snapshot_main_pid"] or -1)
                     and fields.get("id") == cr["unit"])
    if not same_identity:
        _evi(change_run_id, cr["target_id"], "systemd_change:aborted_before_apply",
             {"reason": "pre-apply drift", "definite": definite, "fields": fields}, exit_status)
        return _final(change_run_id, token, "aborted_before_apply", "pre-apply drift: identity changed")

    # 2) the fixed privileged restart.
    try:
        res = runner(transport.apply_argv(target), transport.APPLY_TIMEOUT)
    except subprocess.TimeoutExpired:
        _evi(change_run_id, cr["target_id"], "systemd_change:apply",
             {"outcome": "timeout"}, "timeout")
        return _mark_unknown(change_run_id, token, "apply ssh timeout — outcome unknown")
    except OSError as exc:
        _evi(change_run_id, cr["target_id"], "systemd_change:apply",
             {"outcome": "ssh_launch_error", "error": str(exc)}, "error")
        return _final(change_run_id, token, "command_failed", "ssh could not launch (no restart ran)")

    if res.exit_code == 255:                # ssh transport-level failure — ambiguous
        _evi(change_run_id, cr["target_id"], "systemd_change:apply", {"outcome": "ssh_255"}, "255")
        return _mark_unknown(change_run_id, token, "ssh transport error (255) — outcome unknown")
    if res.exit_code != 0:                  # remote command definitely returned non-zero
        _evi(change_run_id, cr["target_id"], "systemd_change:apply",
             {"outcome": "nonzero", "stderr": _cap(res.stderr)}, str(res.exit_code))
        return _final(change_run_id, token, "command_failed", f"restart exited {res.exit_code}")
    _evi(change_run_id, cr["target_id"], "systemd_change:apply", {"outcome": "exit0"}, "0")

    # 3) post-check — bounded poll. active/running + MainPID CHANGED → applied;
    #    definite-but-unhealthy → postcheck_failed; INDEFINITE → apply_unknown.
    snap_pid = int(cr["snapshot_main_pid"] or -1)
    last_definite, last_fields = False, {}
    for i in range(POSTCHECK_ATTEMPTS):
        pdef, _pexit, pf = _inspect(target, runner)
        last_definite, last_fields = pdef, pf
        if pdef and _precondition_ok(pf) and int(pf.get("main_pid") or 0) != snap_pid:
            _evi(change_run_id, cr["target_id"], "systemd_change:postcheck",
                 {"verdict": "applied", "fields": pf}, "0")
            return _final(change_run_id, token, "applied", "active/running, MainPID changed")
        if i < POSTCHECK_ATTEMPTS - 1:
            sleep(POSTCHECK_INTERVAL)
    if last_definite:                       # definite state that never met the criterion
        _evi(change_run_id, cr["target_id"], "systemd_change:postcheck",
             {"verdict": "postcheck_failed", "fields": last_fields}, "0")
        return _final(change_run_id, token, "postcheck_failed", "not active/running or MainPID unchanged")
    _evi(change_run_id, cr["target_id"], "systemd_change:postcheck",
         {"verdict": "apply_unknown", "reason": "post-inspect indefinite"}, "unknown")
    return _mark_unknown(change_run_id, token, "post-inspect indefinite — outcome unknown")


def resolve(change_run_id: str, *, resolver: str, registry_path: str | None = None,
            runner: Runner = transport.run) -> bool:
    """Perform the fresh server-side inspect for a locked `apply_unknown` and hand the
    STRUCTURED fields to the store's fresh-inspect-gated resolution."""
    cr = cs.get_change_run(change_run_id)
    if cr is None or cr["change_run_status"] not in ("apply_unknown", "rollback_failed"):
        return False
    target = registry.resolve(cr["target_id"], path=registry_path)
    if not _binding_matches(cr, target):    # binding drift → no SSH, stays locked
        return False
    if target.target_kind == registry.SQLITE_MIGRATION:
        try:
            snapshot = json.loads(cr.get("snapshot") or "{}")
        except (ValueError, TypeError):
            return False
        _definite, exit_status, fields = database_change.inspect(target)
        resolved = cs.resolve_database_after_inspect(
            change_run_id=change_run_id,
            resolver=resolver,
            inspect_fields=fields,
            inspect_exit_status=exit_status,
        )
        if resolved:
            database_change.cleanup_backup(target, change_run_id)
        return resolved
    if target.target_kind == registry.NETDATA_CONFIG:
        _definite, exit_status, fields = config_change.inspect(target, runner)
        return cs.resolve_config_after_inspect(
            change_run_id=change_run_id, resolver=resolver,
            inspect_fields=fields, inspect_exit_status=exit_status)
    if cr["change_run_status"] != "apply_unknown":
        return False
    _definite, exit_status, fields = _inspect(target, runner)
    return cs.resolve_after_inspect(change_run_id=change_run_id, resolver=resolver,
                                    inspect_fields=fields, inspect_exit_status=exit_status)


def _final(change_run_id: str, token: str, status: str, verdict: str) -> str:
    """Finalize; if the token-guarded CAS LOST (a concurrent deadline sweep already moved
    the run to apply_unknown during a slow post-check), report the ACTUAL DB status — never
    the engine's local decision. The DB is authoritative."""
    if cs.finalize_apply(change_run_id=change_run_id, attempt_token=token, status=status, verdict=verdict):
        return status
    cr = cs.get_change_run(change_run_id)
    return cr["change_run_status"] if cr else status


def _apply_config(cr: dict[str, Any], target: "registry.Target", *, runner: Runner) -> str:
    change_run_id = cr["change_run_id"]
    token = cr["attempt_token"]
    try:
        snapshot = json.loads(cr.get("snapshot") or "{}")
    except (ValueError, TypeError):
        snapshot = {}
    definite, exit_status, current = config_change.inspect(target, runner)
    same_plan = (definite and current.get("before_sha256") == snapshot.get("before_sha256")
                 and current.get("planned_sha256") == snapshot.get("planned_sha256")
                 and current.get("helper_sha256") == snapshot.get("helper_sha256")
                 and int((current.get("service") or {}).get("main_pid") or -1)
                 == int(cr.get("snapshot_main_pid") or -2))
    if not same_plan:
        _evi(change_run_id, cr["target_id"], "config_change:aborted_before_apply",
             {"reason": "pre-apply config/service drift", "definite": definite}, exit_status)
        return _final(change_run_id, token, "aborted_before_apply",
                      "pre-apply config/service drift")
    status, apply_exit, evidence = config_change.apply(
        target, change_run_id=change_run_id,
        before_sha256=snapshot["before_sha256"],
        after_sha256=snapshot["planned_sha256"],
        before_main_pid=int(snapshot["service"]["main_pid"]), runner=runner)
    _evi(change_run_id, cr["target_id"], "config_change:apply", evidence, apply_exit)
    if status == "apply_unknown":
        return _mark_unknown(change_run_id, token,
                             f"config apply outcome unknown: {evidence.get('outcome', 'unknown')}")
    verdicts = {
        "applied": "typed config applied; service healthy",
        "rolled_back": "post-check failed; original config restored and service healthy",
        "rollback_failed": "automatic rollback failed; target remains locked",
        "aborted_before_apply": "remote helper rejected drift before write",
        "command_failed": "remote helper failed before a confirmed apply",
    }
    return _final(change_run_id, token, status, verdicts.get(status, status))


def _apply_database(cr: dict[str, Any], target: "registry.Target") -> str:
    change_run_id = cr["change_run_id"]
    token = cr["attempt_token"]
    try:
        snapshot = json.loads(cr.get("snapshot") or "{}")
    except (ValueError, TypeError):
        snapshot = {}
    definite, exit_status, current = database_change.inspect(target)
    if not definite or not database_change.matches_before(current, snapshot, target):
        _evi(change_run_id, cr["target_id"], "database_change:aborted_before_apply",
             {"reason": "pre-apply database drift", "definite": definite}, exit_status)
        return _final(change_run_id, token, "aborted_before_apply",
                      "pre-apply database drift")
    status, apply_exit, evidence = database_change.apply(
        target,
        change_run_id=change_run_id,
        snapshot=snapshot,
    )
    _evi(change_run_id, cr["target_id"], "database_change:apply", evidence, apply_exit)
    if status == "apply_unknown":
        return _mark_unknown(change_run_id, token, "database migration outcome unknown")
    verdicts = {
        "applied": "transaction committed; typed database post-check passed",
        "rolled_back": "database post-check failed; backup restored and verified",
        "rollback_failed": "database post-check failed; automatic restore failed",
        "aborted_before_apply": "database drifted before write",
        "command_failed": "migration failed with the approved state preserved",
    }
    final_status = _final(change_run_id, token, status, verdicts.get(status, status))
    if final_status in {"applied", "rolled_back", "aborted_before_apply", "command_failed"}:
        database_change.cleanup_backup(target, change_run_id)
    return final_status


def _mark_unknown(change_run_id: str, token: str, verdict: str) -> str:
    if cs.mark_apply_unknown(change_run_id=change_run_id, attempt_token=token, verdict=verdict):
        return "apply_unknown"
    cr = cs.get_change_run(change_run_id)
    return cr["change_run_status"] if cr else "apply_unknown"


def _binding_matches(cr: dict[str, Any], target: "registry.Target") -> bool:
    try:
        planned = json.loads(cr.get("planned_binding") or "{}")
    except (ValueError, TypeError):
        return False
    return planned == transport.target_binding(target)


def _evi(change_run_id: str, target_id: str, operation: str, result: dict[str, Any],
         exit_status: str) -> None:
    cs.record_change_evidence(change_run_id=change_run_id, target_id=target_id, operation=operation,
                              result=json.dumps(result, ensure_ascii=False), exit_status=exit_status)


def _cap(raw: bytes, limit: int = 500) -> str:
    return decode_console(raw)[:limit]
